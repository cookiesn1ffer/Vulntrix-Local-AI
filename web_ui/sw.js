/**
 * Vulntrix Service Worker v3.0
 *
 * Strategy:
 *  - Static shell (HTML, JS, CSS, icons): cache-first — fast loads, works offline.
 *  - API calls (/api/*, /ws/*): network-only — always live data.
 *  - Everything else: network-first, fall back to cache if offline.
 *
 * On install the shell assets are pre-cached so the UI loads instantly
 * even when the Ollama/FastAPI server hasn't finished starting.
 */

const CACHE   = 'vulntrix-v3';
const SHELL   = [
    '/',
    '/static/app.js',
    '/static/style.css',
    '/static/logo.svg',
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png',
    '/static/manifest.json',
];

// ── Install: pre-cache the app shell ─────────────────────────────────────────
self.addEventListener('install', (e) => {
    e.waitUntil(
        caches.open(CACHE).then(cache => {
            return cache.addAll(SHELL);
        }).catch(err => {
            // Non-fatal — some assets may not exist yet
            console.warn('[SW] Pre-cache partial failure:', err);
        })
    );
    self.skipWaiting();
});

// ── Activate: clean up old caches ────────────────────────────────────────────
self.addEventListener('activate', (e) => {
    e.waitUntil(
        caches.keys().then(keys =>
            Promise.all(
                keys.filter(k => k !== CACHE).map(k => {
                    console.log('[SW] Deleting old cache:', k);
                    return caches.delete(k);
                })
            )
        )
    );
    self.clients.claim();
});

// ── Fetch: route requests ─────────────────────────────────────────────────────
self.addEventListener('fetch', (e) => {
    const url = new URL(e.request.url);

    // 1. API and WebSocket — always hit the network, never cache
    if (url.pathname.startsWith('/api/') ||
        url.pathname.startsWith('/ws/')) {
        return;   // let browser handle it natively
    }

    // 2. App shell & static assets — cache-first
    if (isShellRequest(url)) {
        e.respondWith(
            caches.match(e.request).then(cached => {
                if (cached) return cached;
                return fetchAndCache(e.request);
            })
        );
        return;
    }

    // 3. Everything else — network-first, fall back to cache
    e.respondWith(
        fetch(e.request)
            .then(res => {
                if (res && res.status === 200) {
                    const clone = res.clone();
                    caches.open(CACHE).then(c => c.put(e.request, clone));
                }
                return res;
            })
            .catch(() => caches.match(e.request))
    );
});

// ── Helpers ───────────────────────────────────────────────────────────────────

function isShellRequest(url) {
    return url.pathname === '/' ||
        url.pathname.startsWith('/static/');
}

function fetchAndCache(request) {
    return fetch(request).then(res => {
        if (!res || res.status !== 200 || res.type === 'opaque') return res;
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(request, clone));
        return res;
    });
}

// ── Background sync / push (stubs for future use) ────────────────────────────
self.addEventListener('message', (e) => {
    if (e.data && e.data.type === 'SKIP_WAITING') {
        self.skipWaiting();
    }
});
