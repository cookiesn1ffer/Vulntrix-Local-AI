// Vulntrix v3.0 — Ultimate Build
// ════════════════════════════════════════════════════════════

// ── State ────────────────────────────────────────────────
let ws            = null;
let wsReady       = false;
let wsRetries     = 0;
const WS_MAX      = 10;
let _pingInterval = null;

let currentTarget  = null;
let msgIdCounter   = 0;
let streamingMsgId = null;
let activeChatMode = 'pentest';   // 'pentest' | 'free'
let _timelineFilter = 'all';
let _activeView = localStorage.getItem('vulntrix_active_view') || 'chat';

// ── Auth ─────────────────────────────────────────────────
let _botToken = sessionStorage.getItem('bot_token') || '';

// ── Boot ─────────────────────────────────────────────────
// ── Markdown renderer ────────────────────────────────────
function renderMarkdown(text) {
    const cleaned = normalizeModelText(text);
    // Keep light normalization only; aggressive bullet restructuring can corrupt
    // code-like outputs (e.g. arithmetic, pointers, shell flags) and appear glitchy.
    const structured = cleaned.replace(/([.!?])\s+(\d+\.\s+)/g, '$1\n$2');
    if (window.marked) {
        try { return marked.parse(structured); } catch (_) {}
    }
    return esc(structured);
}

function normalizeModelText(text) {
    return String(text || '')
        .replace(/\u0000/g, '')
        // Some models emit literal or escaped HTML breaks in plain text.
        .replace(/&amp;lt;br\s*\/?&amp;gt;/gi, '\n')
        .replace(/&lt;br\s*\/?&gt;/gi, '\n')
        .replace(/<br\s*\/?>/gi, '\n');
}

document.addEventListener('DOMContentLoaded', async () => {
    if (window.marked) { marked.setOptions({ breaks: true, gfm: true }); }
    document.getElementById('reportDate').value = new Date().toLocaleDateString();
    await checkAuthGate();   // show login overlay if needed
    connectWebSocket();
    setupEventListeners();
    checkHealth();
    loadAvailableModels();
    fetchVersion();
    applySidebarState();
    showView(_activeView);
});

// ── WebSocket ────────────────────────────────────────────
function connectWebSocket() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const tokenParam = _botToken ? `?token=${encodeURIComponent(_botToken)}` : '';
    ws = new WebSocket(`${proto}//${location.host}/ws/stream${tokenParam}`);
    ws.onopen    = onWsOpen;
    ws.onmessage = onWsMessage;
    ws.onerror   = onWsError;
    ws.onclose   = onWsClose;
}

function onWsOpen() { wsRetries = 0; }

function onWsMessage(event) {
    const data = JSON.parse(event.data);

    if (data.type === 'connected') {
        wsReady = true;
        setDot('ws-dot', 'green');
        document.getElementById('ws-label').textContent = 'Connected';
        startPing();
        return;
    }
    if (data.type === 'pong') return;

    if (data.error) {
        endStream();
        addMsg('error', data.error);
        return;
    }

    if (data.token !== undefined) {
        if (streamingMsgId === null) {
            const box = activeChatMode === 'free' ? 'freeMessages' : 'chatMessages';
            streamingMsgId = addMsg('ai', '', box);
        }
        appendToken(streamingMsgId, data.token);
    }

    if (data.done) endStream();
}

function onWsError() {
    wsReady = false;
    setDot('ws-dot', 'red');
}

function onWsClose() {
    wsReady = false;
    setDot('ws-dot', 'red');
    document.getElementById('ws-label').textContent = 'Reconnecting…';
    endStream();
    if (_pingInterval) { clearInterval(_pingInterval); _pingInterval = null; }
    if (wsRetries < WS_MAX) {
        const delay = Math.min(1000 * 2 ** wsRetries, 30000);
        wsRetries++;
        setTimeout(connectWebSocket, delay);
    } else {
        toast('WebSocket disconnected — refresh the page', 'error');
    }
}

function startPing() {
    if (_pingInterval) clearInterval(_pingInterval);
    _pingInterval = setInterval(() => wsSend({ type: 'ping' }), 25000);
}

function wsSend(obj) {
    if (!wsReady || !ws || ws.readyState !== WebSocket.OPEN) {
        toast('Not connected — reconnecting…', 'warning');
        connectWebSocket();
        return false;
    }
    ws.send(JSON.stringify(obj));
    return true;
}

// ── API helper ───────────────────────────────────────────
async function api(path, method = 'GET', body = null) {
    const opts = { method, headers: {} };
    if (_botToken) opts.headers['X-Bot-Token'] = _botToken;
    if (body) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    const r = await fetch('/api' + path, opts);
    if (r.status === 401) { showLoginOverlay(); return null; }
    if (!r.ok) { const e = await r.json().catch(() => ({ detail: r.statusText })); throw new Error(e.detail || r.statusText); }
    return r.json();
}

// ── Toast notifications ──────────────────────────────────
function toast(msg, type = 'ok', duration = 3500) {
    const container = document.getElementById('toastContainer');
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.textContent = msg;
    container.appendChild(el);
    setTimeout(() => {
        el.style.animation = 'slideOut .25s ease-in forwards';
        setTimeout(() => el.remove(), 250);
    }, duration);
}

// ── Auth gate ─────────────────────────────────────────────
// NOTE: _botToken holds a SERVER-ISSUED SESSION TOKEN (UUID), not the raw
// BOT_SECRET. The raw password is sent once to /api/auth/verify; the server
// returns a session token that expires after SESSION_TTL_HOURS.

let _refreshTimer = null;

async function checkAuthGate() {
    try {
        const r = await fetch('/api/auth/status');
        if (!r.ok) return;
        const data = await r.json();
        if (!data.auth_enabled) return;   // auth off, nothing to do

        // Try to refresh an existing stored session token
        if (_botToken) {
            const rr = await fetch('/api/auth/refresh', {
                method: 'POST',
                headers: { 'X-Bot-Token': _botToken },
            });
            if (rr.ok) {
                document.getElementById('sb-auth').classList.remove('hidden');
                _scheduleRefresh(data.session_ttl_hours || 8);
                return;
            }
            // Token expired — clear it and show login
            _botToken = '';
            sessionStorage.removeItem('bot_token');
        }
        showLoginOverlay();
        await new Promise(resolve => { window._loginResolve = resolve; });
    } catch (_) { /* server unreachable — continue anyway */ }
}

function _scheduleRefresh(ttlHours) {
    // Refresh at 80% of TTL to keep session alive during active use
    if (_refreshTimer) clearTimeout(_refreshTimer);
    const ms = ttlHours * 3600 * 1000 * 0.8;
    _refreshTimer = setTimeout(async () => {
        if (!_botToken) return;
        const r = await fetch('/api/auth/refresh', {
            method: 'POST',
            headers: { 'X-Bot-Token': _botToken },
        });
        if (r.ok) {
            _scheduleRefresh(ttlHours);  // reschedule
        } else {
            toast('Session expired — please log in again', 'warning');
            doLogout();
        }
    }, ms);
}

function showLoginOverlay() {
    document.getElementById('loginOverlay').classList.remove('hidden');
    setTimeout(() => document.getElementById('loginInput').focus(), 50);
}

function hideLoginOverlay() {
    document.getElementById('loginOverlay').classList.add('hidden');
    document.getElementById('loginError').classList.add('hidden');
    document.getElementById('loginInput').value = '';
}

async function doLogin() {
    const secret = document.getElementById('loginInput').value.trim();
    if (!secret) return;
    try {
        const r = await fetch('/api/auth/verify', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ token: secret }),
        });
        const data = await r.json();
        if (data.valid) {
            // Store SESSION TOKEN (not raw password)
            _botToken = data.session_token || '';
            if (_botToken) sessionStorage.setItem('bot_token', _botToken);
            hideLoginOverlay();
            document.getElementById('sb-auth').classList.remove('hidden');
            _scheduleRefresh(data.expires_in ? data.expires_in / 3600 : 8);
            if (window._loginResolve) { window._loginResolve(); window._loginResolve = null; }
        } else {
            document.getElementById('loginError').classList.remove('hidden');
        }
    } catch (e) {
        toast('Login failed: ' + e.message, 'error');
    }
}

async function doLogout() {
    try {
        if (_botToken) {
            await fetch('/api/auth/logout', {
                method: 'POST', headers: { 'X-Bot-Token': _botToken },
            });
        }
    } catch (_) {}
    _botToken = '';
    sessionStorage.removeItem('bot_token');
    if (_refreshTimer) { clearTimeout(_refreshTimer); _refreshTimer = null; }
    document.getElementById('sb-auth').classList.add('hidden');
    showLoginOverlay();
    if (ws) { ws.close(); ws = null; wsReady = false; }
}

async function doWipeAllData() {
    const ok = confirm(
        'This will permanently delete ALL saved targets, notes, credentials, timeline, and cached analysis on this machine. Continue?'
    );
    if (!ok) return;
    try {
        const r = await api('/reset-data', 'POST');
        const deleted = (r && typeof r.targets_deleted === 'number') ? r.targets_deleted : 0;

        // Clear browser-side state too, so UI fully resets.
        currentTarget = null;
        localStorage.removeItem('vulntrix_active_view');
        localStorage.removeItem('vulntrix_sidebar_collapsed');
        sessionStorage.removeItem('bot_token');
        _botToken = '';

        const targetBadge = document.getElementById('currentTargetBadge');
        if (targetBadge) {
            targetBadge.textContent = 'No target selected';
            targetBadge.className = 'target-badge';
        }
        ['chatMessages', 'freeMessages', 'notesList', 'credsList', 'chainList', 'timelineList'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.innerHTML = '';
        });

        toast(`Local data wiped (${deleted} target files removed).`, 'ok', 2600);
        setTimeout(() => location.reload(), 500);
    } catch (e) {
        toast('Wipe failed: ' + e.message, 'error');
    }
}

// ── Health check ─────────────────────────────────────────
async function checkHealth() {
    try {
        const d = await api('/health');
        const ok = d.ollama === 'ok';
        setDot('ollama-dot', ok ? 'green' : 'red');
        document.getElementById('ollama-status').textContent = ok ? 'Ollama OK' : 'Offline';
        document.getElementById('reasoning-status').textContent =
            `Reasoning: ${d.reasoning_model} ${d.reasoning_ok ? '✓' : '✗'}`;
        document.getElementById('coding-status').textContent =
            `Coding: ${d.coding_model} ${d.coding_ok ? '✓' : '✗'}`;
    } catch {
        setDot('ollama-dot', 'red');
        document.getElementById('ollama-status').textContent = 'Offline';
    }
    setTimeout(checkHealth, 15000);
}

// ── Version badge ────────────────────────────────────────
async function fetchVersion() {
    try {
        const d = await api('/version');
        if (d && d.version) {
            const el = document.getElementById('versionBadge');
            if (el) el.textContent = `v${d.version}`;
        }
    } catch (_) {}
}

// ── Stop streaming ───────────────────────────────────────
function doStopStream() {
    // Close the WebSocket — kills the server generator immediately.
    // endStream() finalises the partial response, then reconnect picks up.
    if (ws) { ws.close(); ws = null; wsReady = false; }
    endStream();
    toast('Generation stopped', 'ok', 1500);
    // Reconnect with a short delay so the close settles first.
    setTimeout(connectWebSocket, 300);
}

// ── Models ───────────────────────────────────────────────
async function loadAvailableModels() {
    try {
        const d   = await api('/models');
        const sel = document.getElementById('freeModelSelect');
        sel.innerHTML = '';
        const models = d.models || [];
        const prio = ['dolphin', 'nous', 'uncensored', 'hermes'];
        models.sort((a, b) => {
            const aP = prio.some(p => a.toLowerCase().includes(p)) ? 1 : 0;
            const bP = prio.some(p => b.toLowerCase().includes(p)) ? 1 : 0;
            return bP - aP;
        });
        if (!models.length) {
            sel.innerHTML = '<option>No models pulled yet</option>';
            return;
        }
        models.forEach(m => {
            const opt = document.createElement('option');
            opt.value = m; opt.textContent = m;
            sel.appendChild(opt);
        });
    } catch {}
}

// ── Targets ──────────────────────────────────────────────
async function doSetTarget(target) {
    try {
        await api('/target', 'POST', { target });
        currentTarget = target;
        document.getElementById('currentTargetBadge').textContent = '🎯 ' + target;
        document.getElementById('currentTargetBadge').className = 'target-badge active';
        addMsg('system', `Target set: ${target}`, 'chatMessages');
        toast(`Target set: ${target}`, 'ok');
    } catch (e) { toast(e.message, 'error'); }
}

// ── View switching ───────────────────────────────────────
function showView(name) {
    document.querySelectorAll('.view').forEach(v => v.classList.add('hidden'));
    const el = document.getElementById(`view-${name}`);
    if (el) el.classList.remove('hidden');

    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    const btn = document.querySelector(`[data-view="${name}"]`);
    if (btn) btn.classList.add('active');

    if (name === 'free')     activeChatMode = 'free';
    if (name === 'chat')     activeChatMode = 'pentest';
    if (name === 'notes')    doLoadNotes();
    if (name === 'creds')    doLoadCreds();
    if (name === 'chain')    doLoadChain();
    if (name === 'timeline') doLoadTimeline();
    _activeView = name;
    localStorage.setItem('vulntrix_active_view', name);
}

function applySidebarState() {
    const shell = document.querySelector('.shell');
    if (!shell) return;
    const collapsed = localStorage.getItem('vulntrix_sidebar_collapsed') === '1';
    shell.classList.toggle('sidebar-collapsed', collapsed);
}

function toggleSidebar() {
    const shell = document.querySelector('.shell');
    if (!shell) return;
    const collapsed = !shell.classList.contains('sidebar-collapsed');
    shell.classList.toggle('sidebar-collapsed', collapsed);
    localStorage.setItem('vulntrix_sidebar_collapsed', collapsed ? '1' : '0');
}

function focusPrimaryInput() {
    const inputMap = {
        chat: 'chatInput',
        free: 'freeInput',
        recon: 'pasteInput',
        exploit: 'exploitDetails',
        hash: 'hashInput',
        obfuscate: 'obfsPayload',
        cve: 'cveInput',
        msf: 'msfInput',
        waf: 'wafPayload',
        postex: 'postexContext',
        privesc: 'privescContext',
        wordlist: 'wlCompany',
        phishing: 'phishCompany',
        notes: 'noteLabel',
        creds: 'credUser',
        chain: 'stageName',
        timeline: 'timelineEvent',
        report: 'reportSummary',
    };
    const id = inputMap[_activeView] || 'chatInput';
    const el = document.getElementById(id);
    if (el) el.focus();
}

// ── Messages ─────────────────────────────────────────────
function addMsg(role, text, boxId = null) {
    const targetBox = boxId || (activeChatMode === 'free' ? 'freeMessages' : 'chatMessages');
    const box = document.getElementById(targetBox);
    if (!box) return null;
    const id  = `msg-${msgIdCounter++}`;
    const div = document.createElement('div');
    div.id = id;

    if (role === 'user') {
        div.className = 'msg msg-user';
        div.innerHTML = `<div class="msg-bubble">${esc(text)}</div>`;
    } else if (role === 'ai') {
        div.className = 'msg msg-ai';
        div.innerHTML = `<div class="msg-avatar">AI</div>
            <div class="msg-bubble">
              <button class="copy-btn" onclick="copyMsg('${id}')">copy</button>
              <span class="msg-content"></span><span class="cursor">▌</span>
            </div>`;
    } else if (role === 'error') {
        div.className = 'msg msg-error';
        div.innerHTML = `<div class="msg-sys-inner">⚠ ${esc(text)}</div>`;
    } else {
        div.className = 'msg msg-system';
        div.innerHTML = `<div class="msg-sys-inner">${esc(text)}</div>`;
    }

    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
    return id;
}

function appendToken(msgId, token) {
    const msgEl = document.getElementById(msgId);
    const el = document.querySelector(`#${msgId} .msg-content`);
    if (!msgEl || !el) return;

    const prev = msgEl.dataset.raw || '';
    const chunk = normalizeModelText(token);
    const raw = prev + chunk;
    msgEl.dataset.raw = raw;

    // Avoid rendering partial markdown while streaming (causes visual glitches
    // with unclosed code fences/lists). Keep a stable plaintext preview until done.
    const preview = normalizeModelText(raw).replace(/([.!?])\s+(\d+\.\s+)/g, '$1\n$2');
    el.classList.add('streaming');
    el.textContent = preview;

    const box = el.closest('.chat-messages');
    if (box) box.scrollTop = box.scrollHeight;
}

function endStream() {
    if (streamingMsgId) {
        const cursor = document.querySelector(`#${streamingMsgId} .cursor`);
        if (cursor) cursor.remove();
        // Render collected plain text as markdown
        const msgEl = document.getElementById(streamingMsgId);
        const contentEl = document.querySelector(`#${streamingMsgId} .msg-content`);
        if (contentEl) {
            const raw = (msgEl && msgEl.dataset.raw) ? msgEl.dataset.raw : contentEl.textContent;
            contentEl.classList.remove('streaming');
            contentEl.innerHTML = renderMarkdown(raw);
        }
        if (msgEl) delete msgEl.dataset.raw;
        streamingMsgId = null;
    }
    setSendState(false);
}

function copyMsg(msgId) {
    const el = document.querySelector(`#${msgId} .msg-content`);
    if (el) copyText(el.innerText);
}

function setSendState(loading) {
    ['sendBtn', 'freeSendBtn'].forEach(id => {
        const btn = document.getElementById(id);
        if (!btn) return;
        btn.disabled    = loading;
        btn.textContent = loading ? '…' : 'Send ⏎';
    });
    // Show/hide stop buttons in both chat panels
    ['stopBtn', 'freeStopBtn'].forEach(id => {
        const btn = document.getElementById(id);
        if (btn) btn.classList.toggle('hidden', !loading);
    });
}

function setDot(id, state) {
    const d = document.getElementById(id);
    if (d) d.className = `dot ${state}`;
}

function esc(t) {
    return String(t)
        .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
        .replace(/"/g,'&quot;').replace(/\n/g,'<br>');
}

// Copy to clipboard helper
function copyText(text) {
    if (!navigator.clipboard || !navigator.clipboard.writeText) {
        toast('Clipboard unavailable in this browser context', 'warning', 2200);
        return;
    }
    navigator.clipboard.writeText(text).then(
        () => toast('Copied!', 'ok', 1800),
        () => toast('Copy failed', 'error', 1800)
    );
}

// Copy store — avoids HTML attribute escaping issues with quotes/newlines
const _copyStore = {};

function makeCopyBtn(text, label = 'copy') {
    const key = 'cp_' + (++msgIdCounter);
    _copyStore[key] = text;
    return `<button class="copy-inline" onclick="copyStored('${key}')">${label}</button>`;
}

function copyStored(key) {
    const text = _copyStore[key];
    if (text !== undefined) copyText(text);
}

function storeCopyValue(text) {
    const key = 'cp_' + (++msgIdCounter);
    _copyStore[key] = text;
    return key;
}

// ── Pentest Chat ─────────────────────────────────────────
function sendChatMessage() {
    const inp  = document.getElementById('chatInput');
    const text = inp.value.trim();
    if (!text) return;
    inp.value = '';

    addMsg('user', text, 'chatMessages');
    setSendState(true);

    let mode   = 'analyse';
    let prompt = text;
    if (text.startsWith('/code '))    { mode = 'code';    prompt = text.slice(6); }
    if (text.startsWith('/exploit'))  { mode = 'code'; }
    if (text.startsWith('/analyse ')) { mode = 'analyse'; prompt = text.slice(9); }

    const full = currentTarget ? `[Active target: ${currentTarget}]\n${prompt}` : prompt;
    const sent = wsSend({ mode, prompt: full });
    if (!sent) setSendState(false);
}

function insertChat(text) {
    // Switch to Pentest Chat tab, pre-fill the input, and send immediately
    showView('chat');
    const inp = document.getElementById('chatInput');
    inp.value = text;
    inp.focus();
    // Small delay so the view transition completes before sending
    setTimeout(() => sendChatMessage(), 50);
}

// ── Free Chat ────────────────────────────────────────────
function sendFreeMessage() {
    const inp  = document.getElementById('freeInput');
    const text = inp.value.trim();
    if (!text) return;
    inp.value = '';

    addMsg('user', text, 'freeMessages');
    setSendState(true);

    const model  = document.getElementById('freeModelSelect').value;
    const system = document.getElementById('freeSystemPrompt').value.trim() || null;
    const temp   = parseFloat(document.getElementById('freeTempInput').value) || 0.8;

    const sent = wsSend({ mode: 'free', prompt: text, model, system, temperature: temp });
    if (!sent) setSendState(false);
}

// ── Recon Upload ─────────────────────────────────────────
let selectedFile = null;

function clearFile() {
    selectedFile = null;
    document.getElementById('scanFileInput').value = '';
    document.getElementById('filePreview').classList.add('hidden');
    document.getElementById('dropZone').style.opacity = '1';
}

function clearPaste() { document.getElementById('pasteInput').value = ''; }

async function doUploadScan() {
    if (!currentTarget) { toast('Set a target first', 'error'); return; }

    const pasteText = document.getElementById('pasteInput').value.trim();
    const toolHint  = document.getElementById('reconToolHint').value;

    if (!selectedFile && !pasteText) { toast('Upload a file or paste output first', 'error'); return; }

    setBtnLoading('uploadBtn', true, '🔍 Analysing…');
    activeChatMode = 'pentest';
    showView('chat');

    try {
        let prompt = '';

        if (selectedFile) {
            const fd = new FormData();
            fd.append('file', selectedFile);
            const r = await fetch('/api/recon/file', { method: 'POST', headers: { 'X-Bot-Token': _botToken }, body: fd });
            if (!r.ok) throw new Error((await r.json()).detail);
            const d = await r.json();

            const qualityEmoji = { High:'🟢', Medium:'🟡', Low:'🔴', Empty:'⚫' };
            const emoji = qualityEmoji[d.scan_quality] || '🔵';
            addMsg('system',
                `${emoji} Quality: ${d.scan_quality || 'unknown'} · ` +
                `${d.open_ports ?? '?'} open port(s) · ` +
                `${d.noise_lines ?? '?'} noise lines stripped · ` +
                `target: ${d.target || currentTarget}`
            );
            if (d.scan_quality === 'Empty' || d.open_ports === 0) {
                addMsg('system', '⚠ No open ports confirmed — AI will advise on scan coverage.');
            }
            prompt = d.prompt;
        } else {
            const r = await api('/recon/paste', 'POST', { text: pasteText, tool_hint: toolHint, target: currentTarget });
            const qualityEmoji = { High:'🟢', Medium:'🟡', Low:'🔴', Empty:'⚫' };
            const emoji = qualityEmoji[r.scan_quality] || '🔵';
            if (r.scan_quality) {
                addMsg('system',
                    `${emoji} Quality: ${r.scan_quality} · ` +
                    `${r.open_ports ?? '?'} open port(s) · ` +
                    `${r.noise_lines ?? '?'} noise lines stripped`
                );
            }
            addMsg('system', `Analysing ${r.tool_type} output…`);
            prompt = r.prompt;
        }

        setSendState(true);
        const sent = wsSend({ mode: 'analyse', prompt });
        if (!sent) setSendState(false);
        clearFile();
        clearPaste();
    } catch (e) {
        addMsg('error', e.message);
    } finally {
        setBtnLoading('uploadBtn', false, '🔍 Analyse with AI');
    }
}

// ── Exploit Generator ────────────────────────────────────
function doGenerateExploit() {
    if (!currentTarget) { toast('Set a target first', 'error'); return; }

    const type    = document.getElementById('exploitType').value;
    const lang    = document.getElementById('exploitLang').value;
    const lhost   = document.getElementById('exploitLhost').value || '10.10.14.1';
    const lport   = document.getElementById('exploitLport').value || '4444';
    const details = document.getElementById('exploitDetails').value;

    const prompt = `Generate a complete, working ${type} exploit written in ${lang}.
Target: ${currentTarget}
LHOST: ${lhost} | LPORT: ${lport}
${details ? 'Additional context: ' + details : ''}
Requirements:
- Provide complete, runnable code — no placeholders or TODOs
- Add inline comments explaining each step
- Include setup / usage instructions and any dependencies
- Include evasion tips / anti-AV notes where applicable
- Show the listener command on the attacker side`;

    activeChatMode = 'pentest';
    showView('chat');
    addMsg('user', `Generate ${type} (${lang}) → ${lhost}:${lport}`, 'chatMessages');
    setSendState(true);
    const sent = wsSend({ mode: 'code', prompt });
    if (!sent) setSendState(false);
}

// ── Hash Cracker ─────────────────────────────────────────
function hashQuick(h) {
    document.getElementById('hashInput').value = h;
    doHashAnalyze();
}

async function doHashAnalyze() {
    const hashVal = document.getElementById('hashInput').value.trim();
    if (!hashVal) { toast('Paste a hash first', 'error'); return; }

    const hashType  = document.getElementById('hashType').value;
    const context   = document.getElementById('hashContext').value.trim();
    const box       = document.getElementById('hashResults');
    box.innerHTML   = `<div class="loading-msg"><span class="spinner"></span>Analysing hash…</div>`;
    setBtnLoading('hashAnalyzeBtn', true, '🔐 Analysing…');

    try {
        const r = await api('/hash/analyze', 'POST', { hash_value: hashVal, hash_type: hashType, context: context || null });
        renderHashResult(r.result, box);
    } catch (e) {
        box.innerHTML = `<div class="loading-msg" style="color:var(--red)">Error: ${esc(e.message)}</div>`;
    } finally {
        setBtnLoading('hashAnalyzeBtn', false, '🔐 Analyse Hash');
    }
}

function renderHashResult(r, box) {
    const tipsList = (r.cracking_tips || []).map(t => `<li>${esc(t)}</li>`).join('');
    const onlineLinks = (r.online_resources || []).map(u => `<a href="${esc(u)}" target="_blank">${esc(u)}</a>`).join('<br>');
    box.innerHTML = `
    <div class="hash-result-card">
      <div class="hash-field"><label>Hash Type</label><value>${esc(r.hash_type||'unknown')}</value></div>
      <div class="hash-field"><label>Confidence</label><value>${esc(r.confidence||'-')}</value></div>
      <div class="hash-field"><label>Hashcat Mode</label><value style="color:var(--accent)">-m ${esc(r.hashcat_mode||'?')}</value></div>
      <div class="hash-field"><label>John Format</label><value style="color:var(--accent)">--format=${esc(r.john_format||'?')}</value></div>
      <div class="hash-field"><label>Salted</label><value>${r.is_salted ? 'Yes ⚠' : 'No'}</value></div>
      <div class="hash-field"><label>Rainbow Tables</label><value>${r.rainbow_table ? 'Possible ✓' : 'No'}</value></div>
      <div class="hash-field" style="grid-column:1/-1"><label>Crack time estimate</label><value>${esc(r.estimated_crack_time||'?')}</value></div>
      <div class="hash-cmd-block">
        <div class="hash-cmd-label">Hashcat</div>
        <div class="hash-cmd">${esc(r.hashcat_command||'')}</div>
      </div>
      <div class="hash-cmd-block">
        <div class="hash-cmd-label">John the Ripper</div>
        <div class="hash-cmd">${esc(r.john_command||'')}</div>
      </div>
      ${tipsList ? `<div class="hash-cmd-block" style="grid-column:1/-1">
        <div class="hash-cmd-label">Cracking Tips</div>
        <ul style="padding-left:16px;margin-top:4px;font-size:12px;color:var(--text2);line-height:1.8">${tipsList}</ul>
      </div>` : ''}
      ${onlineLinks ? `<div class="hash-cmd-block" style="grid-column:1/-1">
        <div class="hash-cmd-label">Online Resources</div>
        <div style="font-size:12px;line-height:1.8;margin-top:4px">${onlineLinks}</div>
      </div>` : ''}
      ${r.notes ? `<div style="grid-column:1/-1;font-size:12px;color:var(--muted);padding:4px 0">${esc(r.notes)}</div>` : ''}
    </div>
    <div style="display:flex;gap:8px;margin-top:6px;flex-wrap:wrap">
      ${makeCopyBtn(r.hashcat_command||'', '📋 Copy hashcat')}
      ${makeCopyBtn(r.john_command||'', '📋 Copy john')}
    </div>`;
}

// ── Payload Obfuscator ───────────────────────────────────
async function doObfuscate() {
    const payload = document.getElementById('obfsPayload').value.trim();
    if (!payload) { toast('Paste a payload first', 'error'); return; }

    const technique = document.getElementById('obfsTechnique').value;
    const language  = document.getElementById('obfsLang').value;
    const box       = document.getElementById('obfsResults');
    box.innerHTML   = `<div class="loading-msg"><span class="spinner"></span>Obfuscating payload…</div>`;
    setBtnLoading('obfsBtn', true, '🎭 Obfuscating…');

    try {
        const r = await api('/payload/obfuscate', 'POST', { payload, technique, language });
        renderObfsResult(r.result, box);
    } catch (e) {
        box.innerHTML = `<div class="loading-msg" style="color:var(--red)">Error: ${esc(e.message)}</div>`;
    } finally {
        setBtnLoading('obfsBtn', false, '🎭 Obfuscate Payload');
    }
}

function renderObfsResult(r, box) {
    const variantsHtml = (r.variants || []).map(v => `
        <div class="obfs-variant">
          <div class="obfs-variant-name">${esc(v.name||v.technique||'Variant')}</div>
          <div class="obfs-payload-block">${esc(v.payload||v.one_liner||'')}</div>
          ${v.decoder_stub ? `<div style="font-size:11px;color:var(--muted);margin-bottom:4px">Decoder stub:</div>
          <div class="obfs-payload-block" style="color:var(--orange)">${esc(v.decoder_stub)}</div>` : ''}
          ${v.bypasses ? `<div class="obfs-bypass">✓ Bypasses: ${esc(v.bypasses)}</div>` : ''}
          <div style="margin-top:7px;display:flex;gap:6px">
            ${makeCopyBtn(v.payload||v.one_liner||'', '📋 Copy payload')}
            ${v.one_liner ? makeCopyBtn(v.one_liner, '📋 One-liner') : ''}
          </div>
        </div>`).join('');

    const tips = (r.evasion_tips || []).map(t => `<li>${esc(t)}</li>`).join('');

    box.innerHTML = `
        ${variantsHtml || '<p class="empty-hint">No variants generated</p>'}
        ${tips ? `<div class="result-card">
          <div class="result-card-title">💡 Evasion Tips</div>
          <ul style="padding-left:16px;margin-top:4px;font-size:12px;color:var(--text2);line-height:1.9">${tips}</ul>
        </div>` : ''}`;
}

// ── CVE Lookup ───────────────────────────────────────────
async function doCveLookup() {
    const query = document.getElementById('cveInput').value.trim();
    if (!query) { toast('Enter a query first', 'error'); return; }

    const box = document.getElementById('cveResults');
    box.innerHTML = `<div class="loading-msg"><span class="spinner"></span>Looking up CVEs…</div>`;
    setBtnLoading('cveLookupBtn', true, 'Searching…');

    try {
        const r = await api('/cve/lookup', 'POST', { query, target: currentTarget });
        renderCveResults(r.results, box);
    } catch (e) {
        box.innerHTML = `<div class="loading-msg" style="color:var(--red)">Error: ${esc(e.message)}</div>`;
    } finally {
        setBtnLoading('cveLookupBtn', false, 'Search CVEs');
    }
}

function cveQuick(q) { document.getElementById('cveInput').value = q; doCveLookup(); }

function renderCveResults(results, box) {
    if (!results || !results.length) {
        box.innerHTML = '<p class="empty-hint">No CVEs found. Try a more specific query.</p>';
        return;
    }
    box.innerHTML = results.map(r => {
        const sev = (r.severity || 'medium').toLowerCase();
        return `
        <div class="result-card severity-${sev}">
          <div class="result-card-header">
            <div class="result-card-title">${esc(r.id||'CVE')} — ${esc(r.title||'')}</div>
            <div style="display:flex;gap:6px;align-items:center">
              ${r.cvss ? `<span style="font-size:12px;font-weight:700;color:var(--accent)">CVSS ${r.cvss}</span>` : ''}
              <span class="severity-badge badge-${sev}">${esc(r.severity||'medium')}</span>
            </div>
          </div>
          <div class="result-card-body">${esc(r.description||'')}${r.exploit ? `\n\n🎯 Exploit: ${esc(r.exploit)}` : ''}${r.patch ? `\n\n🩹 Patch: ${esc(r.patch)}` : ''}</div>
          <div style="margin-top:8px;display:flex;gap:6px">
            ${makeCopyBtn(r.id||'', '📋 Copy CVE ID')}
            <button class="copy-inline" onclick="insertChat('Tell me how to exploit ${esc(r.id)} on my target')">💬 Ask AI</button>
          </div>
        </div>`;
    }).join('');
}

// ── MSF Modules ──────────────────────────────────────────
async function doMsfSearch() {
    const query = document.getElementById('msfInput').value.trim();
    if (!query) { toast('Enter a query first', 'error'); return; }

    const box = document.getElementById('msfResults');
    box.innerHTML = `<div class="loading-msg"><span class="spinner"></span>Finding modules…</div>`;
    setBtnLoading('msfSearchBtn', true, 'Searching…');

    try {
        const r = await api('/msf/search', 'POST', { query, target: currentTarget });
        renderMsfResults(r.modules, box);
    } catch (e) {
        box.innerHTML = `<div class="loading-msg" style="color:var(--red)">Error: ${esc(e.message)}</div>`;
    } finally {
        setBtnLoading('msfSearchBtn', false, 'Find Modules');
    }
}

function msfQuick(q) { document.getElementById('msfInput').value = q; doMsfSearch(); }

function renderMsfResults(modules, box) {
    if (!modules || !modules.length) {
        box.innerHTML = '<p class="empty-hint">No modules found. Try different keywords.</p>';
        return;
    }
    box.innerHTML = modules.map(m => `
        <div class="result-card">
          <div class="result-card-header">
            <div class="result-card-title">${esc(m.path||m.name||'Module')}</div>
            ${m.type ? `<span class="severity-badge badge-info">${esc(m.type)}</span>` : ''}
          </div>
          <div class="result-card-body">${esc(m.description||'')}</div>
          ${m.command ? `<pre>${esc(m.command)}</pre>` : ''}
          <div style="margin-top:8px;display:flex;gap:6px">
            ${m.command ? makeCopyBtn(m.command, '📋 Copy commands') : ''}
            ${m.path ? makeCopyBtn(`use ${m.path}`, '📋 Copy use') : ''}
          </div>
        </div>`).join('');
}

// ── WAF Evasion ──────────────────────────────────────────
function wafQuick(waf, attack) {
    document.getElementById('wafType').value   = waf;
    document.getElementById('wafAttack').value = attack;
    doWafEvade();
}

async function doWafEvade() {
    const waf_type    = document.getElementById('wafType').value;
    const attack_type = document.getElementById('wafAttack').value;
    const payload     = document.getElementById('wafPayload').value.trim();
    const box         = document.getElementById('wafResults');
    box.innerHTML     = `<div class="loading-msg"><span class="spinner"></span>Generating bypass techniques…</div>`;
    setBtnLoading('wafBtn', true, '🛡 Generating…');

    try {
        const r = await api('/waf/evade', 'POST', { waf_type, attack_type, payload: payload||'', target: currentTarget||null });
        renderWafResult(r.result, box);
    } catch (e) {
        box.innerHTML = `<div class="loading-msg" style="color:var(--red)">Error: ${esc(e.message)}</div>`;
    } finally {
        setBtnLoading('wafBtn', false, '🛡 Generate Bypass Techniques');
    }
}

function renderWafResult(r, box) {
    const techniques = (r.techniques || []).map(t => `
        <div class="waf-technique">
          <div class="waf-tech-name">${esc(t.name||'Technique')}</div>
          <div class="waf-tech-desc">${esc(t.description||'')}</div>
          ${t.example ? `<div class="waf-example">${esc(t.example)}</div>` : ''}
          ${t.effectiveness ? `<div style="font-size:11px;color:var(--muted);margin-top:5px">Effectiveness: ${esc(t.effectiveness)}</div>` : ''}
          ${t.example ? makeCopyBtn(t.example, '📋 Copy') : ''}
        </div>`).join('');

    const encoded = (r.encoded_payloads || []).map(e => `
        <div class="result-card severity-high">
          <div class="result-card-title">${esc(e.encoding||'Encoded')}</div>
          <pre>${esc(e.encoded||'')}</pre>
          ${e.context ? `<div class="result-card-body" style="margin-top:5px">${esc(e.context)}</div>` : ''}
          ${makeCopyBtn(e.encoded||'', '📋 Copy encoded')}
        </div>`).join('');

    const tips = (r.bypass_tips || []).map(t => `<li>${esc(t)}</li>`).join('');
    const tools = (r.tool_commands || []).map(c => `<div class="postex-cmd">${esc(c)}</div>`).join('');

    box.innerHTML = `
        ${techniques}
        ${encoded ? `<div style="font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin-top:4px">Encoded Payloads</div>${encoded}` : ''}
        ${tips ? `<div class="result-card"><div class="result-card-title">💡 Bypass Tips</div>
          <ul style="padding-left:16px;margin-top:4px;font-size:12px;color:var(--text2);line-height:1.9">${tips}</ul></div>` : ''}
        ${tools ? `<div class="result-card"><div class="result-card-title">🔧 Tool Commands</div>${tools}</div>` : ''}`;
}

// ── Post-Exploitation ────────────────────────────────────
async function doPostEx() {
    const os_type      = document.getElementById('postexOS').value;
    const access_level = document.getElementById('postexAccess').value;
    const context      = document.getElementById('postexContext').value.trim();
    const goals        = [...document.querySelectorAll('.checkbox-grid input:checked')].map(cb => cb.value);
    if (!goals.length) { toast('Select at least one goal', 'error'); return; }

    const box = document.getElementById('postexResults');
    box.innerHTML = `<div class="loading-msg"><span class="spinner"></span>Building post-ex command set…</div>`;
    setBtnLoading('postexBtn', true, '🕵 Building…');

    try {
        const r = await api('/postex/build', 'POST', { os_type, access_level, goals, context: context||null });
        renderPostexResult(r.result, box);
    } catch (e) {
        box.innerHTML = `<div class="loading-msg" style="color:var(--red)">Error: ${esc(e.message)}</div>`;
    } finally {
        setBtnLoading('postexBtn', false, '🕵 Build Command Set');
    }
}

function renderPostexResult(r, box) {
    const sections = (r.sections || []).map(s => `
        <div class="postex-section">
          <div class="postex-section-title">${esc(s.goal||'Section')}</div>
          ${(s.commands||[]).map(c => `
            <div class="postex-cmd-item">
              <div class="postex-cmd-desc">${esc(c.description||'')}</div>
              <div class="postex-cmd">${esc(c.command||'')}</div>
              ${c.output_to_look_for ? `<div class="postex-cmd-why">👀 Look for: ${esc(c.output_to_look_for)}</div>` : ''}
              ${c.why ? `<div class="postex-cmd-why">${esc(c.why)}</div>` : ''}
              ${makeCopyBtn(c.command||'', '📋 Copy')}
            </div>`).join('')}
        </div>`).join('');

    const oneShots = (r.one_shot_scripts || []).map(s => `
        <div class="postex-cmd-item">
          <div class="postex-cmd-desc">${esc(s.name||'')} — ${esc(s.description||'')}</div>
          <div class="postex-cmd">${esc(s.command||'')}</div>
          ${makeCopyBtn(s.command||'', '📋 Copy')}
        </div>`).join('');

    const cleanup = (r.cleanup_commands || []).map(c => `<div class="postex-cmd">${esc(c)}</div>`).join('');
    const persist = (r.persistence_methods || []).map(m => `<li>${esc(m)}</li>`).join('');

    box.innerHTML = `
        ${sections}
        ${oneShots ? `<div class="postex-section"><div class="postex-section-title">⚡ One-Shot Scripts</div>${oneShots}</div>` : ''}
        ${persist ? `<div class="result-card"><div class="result-card-title">🔁 Persistence Methods</div>
          <ul style="padding-left:16px;margin-top:4px;font-size:12px;color:var(--text2);line-height:1.9">${persist}</ul></div>` : ''}
        ${cleanup ? `<div class="result-card"><div class="result-card-title">🧹 Cleanup Commands</div>${cleanup}</div>` : ''}`;
}

// ── PrivEsc Checklist ────────────────────────────────────
async function doPrivesc() {
    const os_type      = document.getElementById('privescOS').value;
    const current_user = document.getElementById('privescUser').value.trim() || 'www-data';
    const context      = document.getElementById('privescContext').value.trim();
    const box          = document.getElementById('privescResults');
    box.innerHTML      = `<div class="loading-msg"><span class="spinner"></span>Generating checklist…</div>`;
    setBtnLoading('privescBtn', true, '⬆ Generating…');

    try {
        const r = await api('/privesc/checklist', 'POST', { os_type, current_user, context: context||null });
        renderPrivescResult(r.result, box);
    } catch (e) {
        box.innerHTML = `<div class="loading-msg" style="color:var(--red)">Error: ${esc(e.message)}</div>`;
    } finally {
        setBtnLoading('privescBtn', false, '⬆ Generate Checklist');
    }
}

function renderPrivescResult(r, box) {
    const cats = (r.categories || []).map((cat, ci) => {
        const checks = (cat.checks || []).map((chk, chi) => {
            const checkId = `privesc-${ci}-${chi}`;
            return `
            <div class="privesc-check" onclick="toggleCheck('${checkId}')">
              <input type="checkbox" id="${checkId}">
              <div>
                <div class="privesc-check-title">${esc(chk.title||'Check')}</div>
                ${chk.command ? `<div class="privesc-check-cmd">${esc(chk.command)}</div>` : ''}
                ${chk.what_to_look_for ? `<div class="privesc-check-hint">👀 Look for: ${esc(chk.what_to_look_for)}</div>` : ''}
                ${chk.exploit_if_found ? `<div class="privesc-check-hint" style="color:var(--orange)">⚡ Exploit: ${esc(chk.exploit_if_found)}</div>` : ''}
                ${chk.command ? `<div style="margin-top:5px">${makeCopyBtn(chk.command, '📋 Copy command')}</div>` : ''}
              </div>
            </div>`;
        }).join('');
        const pri = cat.priority || 'medium';
        return `
        <div class="privesc-category">
          <div class="privesc-cat-header">
            <span>${esc(cat.name||'Category')}</span>
            <span class="privesc-priority pri-${pri}">${pri}</span>
          </div>
          ${checks}
        </div>`;
    }).join('');

    const tools = (r.automated_tools || []).map(t => `
        <div class="postex-cmd-item">
          <div class="postex-cmd-desc">${esc(t.name||'')} — ${esc(t.notes||'')}</div>
          <div class="postex-cmd">${esc(t.command||'')}</div>
          ${makeCopyBtn(t.command||'', '📋 Copy')}
        </div>`).join('');

    const quick = (r.quick_wins || []).map(q => `<div class="postex-cmd">${esc(q)}</div>`).join('');

    box.innerHTML = `
        ${quick ? `<div class="result-card" style="border-left:3px solid var(--green)">
          <div class="result-card-title">⚡ Quick Wins — Run These First</div>
          ${quick}
        </div>` : ''}
        ${cats}
        ${tools ? `<div class="postex-section"><div class="postex-section-title">🔧 Automated Tools</div>${tools}</div>` : ''}`;
}

function toggleCheck(id) {
    const cb = document.getElementById(id);
    if (cb) {
        cb.checked = !cb.checked;
        const card = cb.closest('.privesc-check');
        if (card) card.classList.toggle('checked', cb.checked);
    }
}

// ── Wordlist Generator ───────────────────────────────────
async function doWordlist() {
    const company  = document.getElementById('wlCompany').value.trim();
    const domain   = document.getElementById('wlDomain').value.trim();
    const names    = document.getElementById('wlNames').value.trim();
    const keywords = document.getElementById('wlKeywords').value.trim();
    const style    = document.querySelector('input[name="wlStyle"]:checked')?.value || 'passwords';
    if (!company && !names && !keywords) { toast('Fill in at least one field', 'error'); return; }

    const box = document.getElementById('wordlistResults');
    box.innerHTML = `<div class="loading-msg"><span class="spinner"></span>Generating wordlist…</div>`;
    setBtnLoading('wordlistBtn', true, '📋 Generating…');

    try {
        const r = await api('/wordlist/generate', 'POST', { company, domain, names, keywords, style });
        renderWordlistResult(r.result, box);
    } catch (e) {
        box.innerHTML = `<div class="loading-msg" style="color:var(--red)">Error: ${esc(e.message)}</div>`;
    } finally {
        setBtnLoading('wordlistBtn', false, '📋 Generate Wordlist');
    }
}

function renderWordlistResult(r, box) {
    const words = (r.words || []).join('\n');
    const dlKey = storeCopyValue(words);
    const patterns = (r.patterns_used || []).map(p => `<li>${esc(p)}</li>`).join('');
    const masks = (r.hashcat_masks || []).map(m => `<div class="postex-cmd">${esc(m)}</div>`).join('');
    const rules = (r.recommended_rules || []).join(', ');

    box.innerHTML = `
        <div class="wordlist-container">
          <div class="wordlist-meta">
            <span>📋 <b>${(r.words||[]).length}</b> words</span>
            <span>Style: <b>${esc(r.style||'')}</b></span>
            ${makeCopyBtn(words, '📋 Copy all words')}
            <button class="copy-inline" onclick="downloadStoredWordlist('${dlKey}')">⬇ Download .txt</button>
          </div>
          <div class="wordlist-words">${esc(words)}</div>
          ${patterns ? `<div class="result-card"><div class="result-card-title">🧠 Patterns Used</div>
            <ul style="padding-left:16px;margin-top:4px;font-size:12px;color:var(--text2);line-height:1.9">${patterns}</ul></div>` : ''}
          ${masks ? `<div class="result-card"><div class="result-card-title">🎭 Hashcat Masks</div>${masks}</div>` : ''}
          ${rules ? `<div class="result-card"><div class="result-card-title">📜 Recommended Rules</div>
            <div style="font-size:12px;color:var(--text2);margin-top:4px">${esc(rules)}</div></div>` : ''}
          ${r.spray_order ? `<div class="result-card severity-warning"><div class="result-card-title">🎯 Spray Order</div>
            <div class="result-card-body">${esc(r.spray_order)}</div></div>` : ''}
        </div>`;
}

function downloadWordlist(content) {
    const blob = new Blob([content], { type: 'text/plain' });
    const a    = document.createElement('a');
    a.href     = URL.createObjectURL(blob);
    a.download = `wordlist-${currentTarget||'target'}-${Date.now()}.txt`;
    a.click();
}

function downloadStoredWordlist(key) {
    const content = _copyStore[key] || '';
    downloadWordlist(content);
}

// ── Phishing ─────────────────────────────────────────────
async function doPhishing() {
    const company = document.getElementById('phishCompany').value.trim();
    if (!company) { toast('Enter a target company', 'error'); return; }

    const role    = document.getElementById('phishRole').value.trim() || 'employee';
    const pretext = document.getElementById('phishPretext').value;
    const goal    = document.getElementById('phishGoal').value;
    const box     = document.getElementById('phishResults');
    box.innerHTML = `<div class="loading-msg"><span class="spinner"></span>Generating phishing templates…</div>`;
    setBtnLoading('phishBtn', true, '🎣 Generating…');

    try {
        const r = await api('/phishing/generate', 'POST', { company, role, pretext, goal });
        renderPhishResult(r.result, box);
    } catch (e) {
        box.innerHTML = `<div class="loading-msg" style="color:var(--red)">Error: ${esc(e.message)}</div>`;
    } finally {
        setBtnLoading('phishBtn', false, '🎣 Generate Templates');
    }
}

function renderPhishResult(r, box) {
    const templates = (r.templates || []).map(t => `
        <div class="phish-template">
          <div class="phish-template-name">${esc(t.name||'Template')}</div>
          <div class="phish-email-header">
            ${t.subject ? `<span class="lbl">Subject:</span><span class="val">${esc(t.subject)}</span>` : ''}
            ${t.sender_name ? `<span class="lbl">From:</span><span class="val">${esc(t.sender_name)} &lt;${esc(t.sender_email||'')}&gt;</span>` : ''}
          </div>
          <div class="phish-body">${esc(t.body||'')}</div>
          ${t.call_to_action ? `<div style="font-size:11px;color:var(--accent);margin-top:7px">📣 CTA: ${esc(t.call_to_action)}</div>` : ''}
          ${t.landing_page_hint ? `<div style="font-size:11px;color:var(--muted);margin-top:3px">🖥 Landing page: ${esc(t.landing_page_hint)}</div>` : ''}
          <div style="margin-top:5px">${makeCopyBtn(t.body||'', '📋 Copy email body')}</div>
        </div>`).join('');

    const infra = (r.infrastructure_setup || []).map(i => `<li>${esc(i)}</li>`).join('');
    const opsec = (r.opsec_tips || []).map(o => `<li>${esc(o)}</li>`).join('');
    const scripts = (r.pretexting_scripts || []).map(s => `
        <div class="postex-cmd-item">
          <div class="postex-cmd-desc">${esc(s.channel||'Channel')}</div>
          <div class="phish-body">${esc(s.script||'')}</div>
          ${makeCopyBtn(s.script||'', '📋 Copy script')}
        </div>`).join('');

    box.innerHTML = `
        ${templates}
        ${scripts ? `<div class="postex-section"><div class="postex-section-title">📞 Pretexting Scripts</div>${scripts}</div>` : ''}
        ${infra ? `<div class="result-card"><div class="result-card-title">🔧 Infrastructure Setup</div>
          <ul style="padding-left:16px;margin-top:4px;font-size:12px;color:var(--text2);line-height:1.9">${infra}</ul></div>` : ''}
        ${opsec ? `<div class="result-card severity-warning"><div class="result-card-title">🔒 OpSec Tips</div>
          <ul style="padding-left:16px;margin-top:4px;font-size:12px;color:var(--yellow);line-height:1.9">${opsec}</ul></div>` : ''}
        ${r.legal_reminder ? `<div class="callout-warning" style="margin-top:4px">⚠ ${esc(r.legal_reminder)}</div>` : ''}`;
}

// ── Notes ────────────────────────────────────────────────
async function doAddNote() {
    const label   = document.getElementById('noteLabel').value.trim();
    const content = document.getElementById('noteContent').value.trim();
    if (!label || !content) { toast('Label and content required', 'error'); return; }
    try {
        await api('/note', 'POST', { label, content });
        toast('Note saved', 'ok');
        document.getElementById('noteLabel').value = '';
        document.getElementById('noteContent').value = '';
        doLoadNotes();
    } catch (e) { toast(e.message, 'error'); }
}

async function doLoadNotes() {
    if (!currentTarget) return;
    try {
        const d     = await api(`/target/${currentTarget}/context`);
        const list  = document.getElementById('notesList');
        const notes = d.notes || {};
        list.innerHTML = Object.keys(notes).length
            ? Object.entries(notes).map(([k,v]) => `
                <div class="list-card">
                  <div class="list-card-label">📝 ${esc(k)}</div>
                  <div class="list-card-value">${esc(v.slice(0,400))}${v.length>400?'…':''}</div>
                  <div style="margin-top:7px;display:flex;gap:5px">
                    ${makeCopyBtn(v, '📋 Copy')}
                    <button class="btn-danger" onclick="doDeleteNoteByKey('${encodeURIComponent(k)}')">Delete</button>
                  </div>
                </div>`).join('')
            : '<p class="empty-hint">No notes yet.</p>';
    } catch {}
}

async function doDeleteNote(label) {
    try {
        await api(`/note/${encodeURIComponent(label)}`, 'DELETE');
        toast('Note deleted', 'ok');
        doLoadNotes();
    } catch (e) { toast(e.message, 'error'); }
}

function doDeleteNoteByKey(encoded) {
    doDeleteNote(decodeURIComponent(encoded));
}

// ── Credentials ──────────────────────────────────────────
async function doAddCred() {
    const username = document.getElementById('credUser').value.trim();
    const password = document.getElementById('credPass').value.trim();
    const service  = document.getElementById('credService').value.trim();
    if (!username) { toast('Username required', 'error'); return; }
    if (!currentTarget) { toast('Set a target first', 'error'); return; }
    try {
        await api('/credential', 'POST', { target: currentTarget, username, password, service });
        toast('Credential saved', 'ok');
        document.getElementById('credUser').value = '';
        document.getElementById('credPass').value = '';
        document.getElementById('credService').value = '';
        doLoadCreds();
    } catch (e) { toast(e.message, 'error'); }
}

async function doLoadCreds() {
    if (!currentTarget) return;
    try {
        const d     = await api(`/target/${currentTarget}/context`);
        const list  = document.getElementById('credsList');
        const creds = d.credentials || [];
        list.innerHTML = creds.length
            ? creds.map(c => `
                <div class="list-card">
                  <div class="list-card-label">👤 ${esc(c.username)}${c.service?' @ '+esc(c.service):''}</div>
                  <div class="list-card-value">${esc(c.password || c.hash_val || '(no password)')}</div>
                  <div style="margin-top:7px;display:flex;gap:5px">
                    ${makeCopyBtn(c.password||c.hash_val||'', '📋 Copy')}
                    <button class="copy-inline" onclick="doHashFrom('${esc(c.password||c.hash_val||'')}')">🔐 Crack hash</button>
                  </div>
                </div>`).join('')
            : '<p class="empty-hint">No credentials yet.</p>';
    } catch {}
}

function doHashFrom(hash) {
    document.getElementById('hashInput').value = hash;
    showView('hash');
}

// ── Attack Chain ─────────────────────────────────────────
async function doAddStage() {
    const name = document.getElementById('stageName').value.trim();
    if (!name) return;
    try {
        await api('/attack-stage', 'POST', { stage: name, status: 'pending' });
        document.getElementById('stageName').value = '';
        doLoadChain();
    } catch (e) { toast(e.message, 'error'); }
}

function addPresetStage(name) {
    document.getElementById('stageName').value = name;
    doAddStage();
}

async function doMarkStage(name, status) {
    try { await api('/attack-stage', 'POST', { stage: name, status }); doLoadChain(); }
    catch (e) { toast(e.message, 'error'); }
}

async function doLoadChain() {
    if (!currentTarget) return;
    try {
        const d     = await api(`/target/${currentTarget}/context`);
        const list  = document.getElementById('chainList');
        const chain = d.attack_chain || [];
        const icons = { pending: '⏳', in_progress: '🔄', done: '✅', failed: '❌' };
        list.innerHTML = chain.length
            ? chain.map(s => `
                <div class="chain-card chain-${s.status}">
                  <div class="chain-icon">${icons[s.status]||'⏳'}</div>
                  <div class="chain-info">
                    <div class="chain-name">${esc(s.name)}</div>
                    ${s.notes?`<div class="chain-notes">${esc(s.notes)}</div>`:''}
                  </div>
                  <div class="chain-actions">
                    <button onclick="doMarkStageByName('${encodeURIComponent(s.name)}','in_progress')" title="In Progress">🔄</button>
                    <button onclick="doMarkStageByName('${encodeURIComponent(s.name)}','done')"        title="Done">✅</button>
                    <button onclick="doMarkStageByName('${encodeURIComponent(s.name)}','failed')"      title="Failed">❌</button>
                  </div>
                </div>`).join('')
            : '<p class="empty-hint">No attack stages yet. Add one above or use presets.</p>';
    } catch {}
}

function doMarkStageByName(encoded, status) {
    doMarkStage(decodeURIComponent(encoded), status);
}

// ── Timeline ─────────────────────────────────────────────
async function doAddTimelineEvent() {
    if (!currentTarget) { toast('Set a target first', 'error'); return; }
    const event    = document.getElementById('timelineEvent').value.trim();
    const category = document.getElementById('timelineCategory').value;
    const severity = document.getElementById('timelineSeverity').value;
    if (!event) { toast('Enter an event description', 'error'); return; }

    try {
        await api('/timeline/event', 'POST', { target: currentTarget, event, category, severity });
        document.getElementById('timelineEvent').value = '';
        toast('Event logged', 'ok');
        doLoadTimeline();
    } catch (e) { toast(e.message, 'error'); }
}

function filterTimeline(f, el) {
    _timelineFilter = f;
    document.querySelectorAll('.timeline-filters .chip').forEach(c => c.classList.remove('chip-active'));
    if (el) el.classList.add('chip-active');
    doLoadTimeline();
}

async function doLoadTimeline() {
    if (!currentTarget) return;
    try {
        const d    = await api(`/timeline/${currentTarget}`);
        const list = document.getElementById('timelineList');
        const timeline = Array.isArray(d) ? d : (d?.events || []);
        let events = timeline.reverse();

        if (_timelineFilter !== 'all') {
            events = events.filter(e =>
                e.category === _timelineFilter ||
                (e.content || '').toLowerCase().includes(`[${_timelineFilter}]`)
            );
        }

        const catColors = {
            recon: 'var(--cyan)',
            exploit: 'var(--orange)',
            privesc: 'var(--red)',
            lateral: 'var(--accent2)',
            exfil: 'var(--pink)',
            cred: 'var(--yellow)',
            note: 'var(--text2)',
            flag: 'var(--green)',
            misc: 'var(--muted)',
        };

        const sevClass = {
            critical: 'severity-critical',
            success:  'severity-success',
            warning:  'severity-warning',
            info:     'severity-info',
        };

        list.innerHTML = events.length
            ? events.map(e => {
                const ts  = e.timestamp ? new Date(e.timestamp).toLocaleString() : '';
                const cat = e.category || 'misc';
                const col = catColors[cat] || 'var(--muted)';
                const content = e.content || '';
                // Parse severity from content [CRITICAL], [SUCCESS] etc
                const sevMatch = content.match(/\[(CRITICAL|SUCCESS|WARNING|INFO)\]/i);
                const sev = sevMatch ? sevMatch[1].toLowerCase() : 'info';
                const cleanContent = content.replace(/\[(CRITICAL|SUCCESS|WARNING|INFO)\]\s*/i, '');
                return `
                <div class="timeline-entry ${sevClass[sev]||''}">
                  <div class="timeline-time">${esc(ts)}</div>
                  <div class="timeline-cat" style="background:${col}22;color:${col};border:1px solid ${col}44">${esc(cat)}</div>
                  <div class="timeline-text">${esc(cleanContent)}</div>
                </div>`;
            }).join('')
            : '<p class="empty-hint">No events logged yet.</p>';
    } catch {}
}

// ── Report ───────────────────────────────────────────────
async function doGenerateReport() {
    if (!currentTarget) { toast('Set a target first', 'error'); return; }

    setBtnLoading('genReportBtn', true, '🤖 Generating…');
    const preview = document.getElementById('reportPreview');
    preview.classList.remove('hidden');
    preview.dataset.markdown = '';
    preview.textContent = 'Generating report — this may take a minute…';

    try {
        const meta = {
            target:   currentTarget,
            title:    document.getElementById('reportTitle').value,
            author:   document.getElementById('reportAuthor').value,
            date:     document.getElementById('reportDate').value,
            severity: document.getElementById('reportSeverity').value,
            summary:  document.getElementById('reportSummary').value,
        };
        const r = await api('/report/generate', 'POST', meta);
        const markdown = String((r && r.report) || '').trim();
        preview.dataset.markdown = markdown;
        preview.innerHTML = renderMarkdown(markdown);
        toast('Report generated', 'ok');
    } catch (e) {
        preview.dataset.markdown = '';
        preview.textContent = 'Error: ' + e.message;
        toast(e.message, 'error');
    } finally {
        setBtnLoading('genReportBtn', false, '🤖 AI Generate Report');
    }
}

function doExportMarkdown() {
    const preview = document.getElementById('reportPreview');
    const content = (preview.dataset.markdown || '').trim();
    if (!content || content.includes('Generating report')) { toast('Generate a report first', 'error'); return; }
    const blob = new Blob([content], { type: 'text/markdown' });
    const a    = document.createElement('a');
    a.href     = URL.createObjectURL(blob);
    a.download = `pentest-report-${currentTarget||'target'}-${Date.now()}.md`;
    a.click();
    toast('Exported!', 'ok');
}

function doPrintReport() {
    const preview = document.getElementById('reportPreview');
    const markdown = (preview.dataset.markdown || '').trim();
    if (!markdown || preview.classList.contains('hidden')) {
        toast('Generate a report first', 'error');
        return;
    }
    const rawHtml = renderMarkdown(markdown);
    const win = window.open('', '_blank', 'width=900,height=700');
    win.document.write(`<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Pentest Report — ${esc(currentTarget||'Target')}</title>
<style>
  body { font-family: 'Segoe UI', Arial, sans-serif; margin: 40px; color: #1a1a1a; background: #fff; }
  h1,h2,h3 { color: #222; border-bottom: 1px solid #ddd; padding-bottom: 6px; }
  p { margin: 8px 0; line-height: 1.6; }
  ul,ol { margin: 8px 0 10px 22px; }
  li { margin: 3px 0; }
  pre  { background: #f4f4f4; border: 1px solid #ccc; padding: 12px; border-radius: 4px; overflow-x: auto; }
  code { background: #f0f0f0; padding: 2px 5px; border-radius: 3px; font-family: monospace; }
  table { border-collapse: collapse; width: 100%; }
  th, td { border: 1px solid #ccc; padding: 8px 12px; text-align: left; }
  th { background: #f0f0f0; }
  @media print {
    body { margin: 20px; }
    @page { margin: 2cm; }
  }
</style></head>
<body>${rawHtml}</body></html>`);
    win.document.close();
    win.focus();
    setTimeout(() => { win.print(); }, 400);
}

// ── Drag & Drop ──────────────────────────────────────────
function setupDropZone() {
    const zone  = document.getElementById('dropZone');
    const input = document.getElementById('scanFileInput');

    input.addEventListener('change', e => {
        if (e.target.files.length) handleFile(e.target.files[0]);
    });
    zone.addEventListener('dragover',  e => { e.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', ()  => { zone.classList.remove('drag-over'); });
    zone.addEventListener('drop', e => {
        e.preventDefault();
        zone.classList.remove('drag-over');
        if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
    });
}

function handleFile(file) {
    selectedFile = file;
    document.getElementById('fileName').textContent = `📄 ${file.name} (${(file.size/1024).toFixed(1)} KB)`;
    document.getElementById('filePreview').classList.remove('hidden');
    document.getElementById('dropZone').style.opacity = '.5';
}

// ── Helpers ──────────────────────────────────────────────
function setBtnLoading(id, loading, label) {
    const b = document.getElementById(id);
    if (!b) return;
    b.disabled    = loading;
    b.textContent = label;
}

// ── Event listeners ──────────────────────────────────────
function setupEventListeners() {
    // Target
    document.getElementById('targetInput').addEventListener('keydown', e => {
        if (e.key === 'Enter') { const v = e.target.value.trim(); if (v) { doSetTarget(v); e.target.value = ''; } }
    });
    document.getElementById('setTargetBtn').addEventListener('click', () => {
        const inp = document.getElementById('targetInput');
        if (inp.value.trim()) { doSetTarget(inp.value.trim()); inp.value = ''; }
    });

    // Pentest chat
    document.getElementById('chatInput').addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChatMessage(); }
    });
    document.getElementById('sendBtn').addEventListener('click', sendChatMessage);

    // Free chat
    document.getElementById('freeSendBtn').addEventListener('click', sendFreeMessage);
    document.getElementById('freeInput').addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendFreeMessage(); }
    });

    // Recon
    document.getElementById('uploadBtn').addEventListener('click', doUploadScan);
    setupDropZone();

    // Exploit
    document.getElementById('generateBtn').addEventListener('click', doGenerateExploit);

    // Hash
    document.getElementById('hashAnalyzeBtn').addEventListener('click', doHashAnalyze);
    document.getElementById('hashInput').addEventListener('keydown', e => { if (e.key === 'Enter') doHashAnalyze(); });

    // Obfuscator
    document.getElementById('obfsBtn').addEventListener('click', doObfuscate);

    // CVE
    document.getElementById('cveLookupBtn').addEventListener('click', doCveLookup);
    document.getElementById('cveInput').addEventListener('keydown', e => { if (e.key === 'Enter') doCveLookup(); });

    // MSF
    document.getElementById('msfSearchBtn').addEventListener('click', doMsfSearch);
    document.getElementById('msfInput').addEventListener('keydown', e => { if (e.key === 'Enter') doMsfSearch(); });

    // WAF
    document.getElementById('wafBtn').addEventListener('click', doWafEvade);

    // PostEx
    document.getElementById('postexBtn').addEventListener('click', doPostEx);

    // PrivEsc
    document.getElementById('privescBtn').addEventListener('click', doPrivesc);

    // Wordlist
    document.getElementById('wordlistBtn').addEventListener('click', doWordlist);

    // Phishing
    document.getElementById('phishBtn').addEventListener('click', doPhishing);

    // Notes
    document.getElementById('addNoteBtn').addEventListener('click', doAddNote);

    // Creds
    document.getElementById('addCredBtn').addEventListener('click', doAddCred);

    // Chain
    document.getElementById('addStageBtn').addEventListener('click', doAddStage);
    document.getElementById('stageName').addEventListener('keydown', e => { if (e.key === 'Enter') doAddStage(); });

    // Timeline
    document.getElementById('addTimelineBtn').addEventListener('click', doAddTimelineEvent);
    document.getElementById('timelineEvent').addEventListener('keydown', e => { if (e.key === 'Enter') doAddTimelineEvent(); });

    // Report
    document.getElementById('genReportBtn').addEventListener('click', doGenerateReport);
    document.getElementById('exportMdBtn').addEventListener('click', doExportMarkdown);

    // Nav
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.addEventListener('click', () => showView(btn.dataset.view));
    });

    // Timeline filter chips handled via onclick="filterTimeline(f, this)" in HTML
    document.getElementById('sidebarToggle')?.addEventListener('click', toggleSidebar);

    // Auto-grow chat textareas for better typing UX.
    ['chatInput', 'freeInput'].forEach((id) => {
        const t = document.getElementById(id);
        if (!t) return;
        const grow = () => {
            t.style.height = 'auto';
            t.style.height = `${Math.min(t.scrollHeight, 180)}px`;
        };
        t.addEventListener('input', grow);
        grow();
    });

    // Keyboard shortcuts:
    // - Ctrl/Cmd + K => focus primary input
    // - Ctrl/Cmd + B => toggle sidebar
    document.addEventListener('keydown', (e) => {
        const mod = e.ctrlKey || e.metaKey;
        if (!mod) return;

        const key = e.key.toLowerCase();
        if (key === 'k') {
            e.preventDefault();
            focusPrimaryInput();
        } else if (key === 'b') {
            e.preventDefault();
            toggleSidebar();
        }
    });
}
