"""
OllamaClient — low-level HTTP wrapper for the Ollama REST API.

All inference goes through this class so every other module stays
decoupled from the transport layer.  Supports both streaming and
blocking responses and returns clean text regardless of which mode
is used.
"""

from __future__ import annotations

import json
import time
from typing import Generator, Optional

import requests


# ─── defaults ────────────────────────────────────────────────────────────────
OLLAMA_BASE_URL  = "http://localhost:11434"
DEFAULT_TIMEOUT  = 120        # seconds per request
STREAM_TIMEOUT   = 300        # longer allowance for streamed generation
MAX_RETRIES      = 3
RETRY_BACKOFF    = 2.0        # seconds; doubles each retry


class OllamaError(Exception):
    """Raised when Ollama returns an error or is unreachable."""


class OllamaClient:
    """
    Thin wrapper around the Ollama /api/generate endpoint.

    Parameters
    ----------
    base_url : str
        Base URL of the running Ollama instance.
    timeout  : int
        Seconds to wait before giving up on a blocking request.
    """

    def __init__(
        self,
        base_url: str = OLLAMA_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout  = timeout
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    # ─── public API ──────────────────────────────────────────────────────────

    def generate(
        self,
        model: str,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = False,
    ) -> str:
        """
        Send a prompt to *model* and return the full response as a string.

        The method retries on transient connection errors up to MAX_RETRIES
        times with exponential back-off.
        """
        payload = self._build_payload(
            model, prompt, system, temperature, max_tokens, stream
        )
        url = f"{self.base_url}/api/generate"

        last_exc: Exception = RuntimeError("No attempt made")
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                if stream:
                    return self._stream_response(url, payload)
                else:
                    return self._blocking_response(url, payload)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                if attempt < MAX_RETRIES:
                    sleep_for = RETRY_BACKOFF ** attempt
                    time.sleep(sleep_for)
            except OllamaError as exc:
                # Retry on transient server errors (503 Service Unavailable, etc.)
                msg = str(exc)
                if any(f"HTTP {c}" in msg for c in ("500", "502", "503", "504")) and attempt < MAX_RETRIES:
                    last_exc = exc
                    time.sleep(RETRY_BACKOFF ** attempt)
                else:
                    raise   # surface model/API errors immediately

        raise OllamaError(
            f"Ollama unreachable after {MAX_RETRIES} attempts "
            f"(model: {payload.get('model', '?')}, url: {self.base_url}).\n"
            f"Last error: {last_exc}"
        )

    def generate_stream(
        self,
        model: str,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> Generator[str, None, None]:
        """
        Yield response tokens as they arrive (SSE-style streaming).

        Use this from the CLI to give live feedback to the user.
        """
        payload = self._build_payload(
            model, prompt, system, temperature, max_tokens, stream=True
        )
        url = f"{self.base_url}/api/generate"
        with self._session.post(url, json=payload, stream=True,
                                timeout=STREAM_TIMEOUT) as resp:
            self._raise_for_status(resp)
            for line in resp.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise OllamaError(chunk["error"])
                yield chunk.get("response", "")
                if chunk.get("done", False):
                    break

    def list_models(self) -> list[str]:
        """Return the names of all locally-available Ollama models."""
        url = f"{self.base_url}/api/tags"
        try:
            resp = self._session.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return [m["name"] for m in data.get("models", [])]
        except Exception as exc:
            raise OllamaError(f"Could not list Ollama models: {exc}") from exc

    def health_check(self) -> bool:
        """Return True if Ollama is reachable, False otherwise."""
        try:
            resp = self._session.get(f"{self.base_url}/", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    # ─── private helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _build_payload(
        model: str,
        prompt: str,
        system: Optional[str],
        temperature: float,
        max_tokens: int,
        stream: bool,
    ) -> dict:
        payload: dict = {
            "model":  model,
            "prompt": prompt,
            "stream": stream,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if system:
            payload["system"] = system
        return payload

    def _blocking_response(self, url: str, payload: dict) -> str:
        resp = self._session.post(url, json=payload, timeout=self.timeout)
        self._raise_for_status(resp)
        data = resp.json()
        if data.get("error"):
            raise OllamaError(data["error"])
        return data.get("response", "").strip()

    def _stream_response(self, url: str, payload: dict) -> str:
        """Consume an SSE stream and return concatenated text."""
        parts: list[str] = []
        with self._session.post(url, json=payload, stream=True,
                                timeout=STREAM_TIMEOUT) as resp:
            self._raise_for_status(resp)
            for line in resp.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise OllamaError(chunk["error"])
                parts.append(chunk.get("response", ""))
                if chunk.get("done", False):
                    break
        return "".join(parts).strip()

    @staticmethod
    def _raise_for_status(resp: requests.Response) -> None:
        if resp.status_code != 200:
            body = resp.text[:400].strip()
            # Try to extract the "error" field from JSON error responses
            try:
                err_json = resp.json()
                if isinstance(err_json, dict) and "error" in err_json:
                    body = err_json["error"]
            except Exception:
                pass
            raise OllamaError(f"HTTP {resp.status_code} from Ollama: {body}")
