# ── Vulntrix — Docker image ───────────────────────────────────────────────────
# Usage:
#   docker build -t vulntrix .
#   docker run -p 8000:8000 \
#     -e BOT_SECRET=changeme \
#     -v ollama-data:/root/.ollama \
#     vulntrix
#
# TLS: mount your certs/ directory:
#   docker run -p 8443:8443 \
#     -v /path/to/certs:/app/certs:ro \
#     -e BOT_SECRET=changeme vulntrix
#
# NOTE: Ollama must be running separately (on host or another container).
#       Set VULNTRIX_OLLAMA_URL=http://host.docker.internal:11434 if needed.

FROM python:3.11-slim

# ── System deps (minimal) ─────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── App directory ─────────────────────────────────────────────────────────────
WORKDIR /app

# ── Dependencies (cached layer) ───────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Source code ───────────────────────────────────────────────────────────────
COPY . .

# ── Non-root user for security ────────────────────────────────────────────────
RUN useradd -m -u 1001 vulntrix && chown -R vulntrix:vulntrix /app
USER vulntrix

# ── Data directory (target context persists here) ─────────────────────────────
RUN mkdir -p /home/vulntrix/.vulntrix/targets
VOLUME ["/home/vulntrix/.vulntrix"]

# ── Expose both HTTP and HTTPS ports ─────────────────────────────────────────
EXPOSE 8000 8443

# ── Health check ─────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:8000/api/health || \
        curl -sfk https://localhost:8443/api/health || exit 1

# ── Start server ──────────────────────────────────────────────────────────────
CMD ["python", "web_server.py"]
