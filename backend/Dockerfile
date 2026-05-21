# ═══════════════════════════════════════════════════════════
# SenHarness backend — multi-stage (base -> dev -> prod)
# Note: omitting `# syntax=docker/dockerfile:1.7` so BuildKit's
# built-in frontend is used (works offline / behind firewalls).
# ═══════════════════════════════════════════════════════════

ARG PYTHON_VERSION=3.12

# ─── base: system deps + uv ───────────────────────────────
FROM python:${PYTHON_VERSION}-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    UV_LINK_MODE=copy \
    UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    UV_HTTP_TIMEOUT=120 \
    VIRTUAL_ENV=/app/.venv \
    PATH=/app/.venv/bin:/root/.local/bin:$PATH
WORKDIR /app
# Use Tsinghua Debian mirror (more reliable from CN networks) + USTC for security.
RUN set -eux; \
    sed -i 's|http://deb.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g; s|http://security.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
    apt-get update && apt-get install -y --no-install-recommends \
      build-essential curl ca-certificates libpq-dev \
    && rm -rf /var/lib/apt/lists/*
# Pull uv binary from GHCR. When network requires proxy, set
# `http_proxy/https_proxy` in the shell before `docker compose build`.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock* /app/
RUN uv sync --frozen --no-install-project --no-editable || uv sync --no-install-project --no-editable

# ─── dev: source mounted at runtime ───────────────────────
FROM base AS dev
# Keep dev image self-contained. `docker-compose.yml` does not bind-mount
# `./backend` into `/app`, so without copying sources this stage would only
# contain pyproject/lock (from base) and fail with `ModuleNotFoundError: app`.
# We still keep `--reload` for local iteration; rebuilding refreshes code.
COPY . /app
RUN uv sync --frozen --no-editable || uv sync --no-editable
ENV APP_ENV=development APP_DEBUG=true
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ─── prod: copy source, non-root user ─────────────────────
FROM base AS prod
ARG APP_USER=senharness
RUN useradd -m -u 10001 ${APP_USER}
COPY --chown=${APP_USER}:${APP_USER} . /app
RUN uv sync --frozen --no-editable || uv sync --no-editable
# Ops scripts may be committed from Windows where Git drops the +x bit.
# We apply it explicitly inside the image so `docker-compose.prod.yml`'s
# `entrypoint: ["/app/scripts/ops/entrypoint.sh"]` works regardless of
# where the image was built.
RUN chmod +x /app/scripts/ops/*.sh 2>/dev/null || true
USER ${APP_USER}
ENV APP_ENV=production APP_DEBUG=false
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=10 --start-period=60s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/v1/readyz', timeout=3).status==200 else 1)"
CMD ["python", "-m", "cli.commands", "server", "run"]
