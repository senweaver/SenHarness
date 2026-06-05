"""Application settings loaded from environment (pydantic-settings)."""

from __future__ import annotations

import socket
from functools import lru_cache
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "staging", "production"]
KeyringProviderKind = Literal[
    "env", "file", "passphrase", "aws_kms", "gcp_kms", "azure_kv", "vault", "hsm"
]


class Settings(BaseSettings):
    """Top-level settings for SenHarness backend."""

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[3] / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ─── App ────────────────────────────────────────────────
    APP_NAME: str = "SenHarness"
    APP_ENV: AppEnv = "development"
    APP_DEBUG: bool = True
    APP_TIMEZONE: str = "Asia/Shanghai"
    APP_DEFAULT_LOCALE: str = "zh-CN"
    API_PREFIX: str = "/api/v1"

    BACKEND_HOST: str = "0.0.0.0"
    BACKEND_PORT: int = 8000
    PUBLIC_BASE_URL: str = "http://localhost:8000"

    # ─── Security ──────────────────────────────────────────
    SENHARNESS_MASTER_KEY: str = ""
    KEYRING_PROVIDER: KeyringProviderKind = "env"
    KEYRING_FILE_PATH: str = "/etc/senharness/keyring.jwks"
    AWS_KMS_KEY_ID: str = ""
    AWS_REGION: str = ""
    GCP_KMS_KEY_NAME: str = ""
    AZURE_KV_URL: str = ""
    AZURE_KV_KEY_NAME: str = ""
    VAULT_ADDR: str = ""
    VAULT_TRANSIT_KEY: str = ""

    JWT_SECRET_KEY: str = "dev-only-do-not-use-in-production!!"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TTL_SECONDS: int = 1800
    JWT_REFRESH_TTL_SECONDS: int = 2592000
    JWT_REFRESH_COOKIE_NAME: str = "sh_refresh"
    JWT_REFRESH_COOKIE_SECURE: bool = False

    CORS_ALLOW_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    # ─── Database ──────────────────────────────────────────
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_USER: str = "senharness"
    DB_PASSWORD: str = "senharness"
    DB_NAME: str = "senharness"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_ECHO: bool = False
    # asyncpg ssl mode: disable | allow | prefer | require | verify-ca | verify-full.
    # Default matches asyncpg (prefer = opportunistic probe). Windows + Docker
    # Desktop's port-proxy tears the probe socket down mid-handshake, which
    # surfaces as ``ConnectionError: unexpected connection_lost()``; set this
    # to ``disable`` for local host-mode dev against the dockerised Postgres.
    DB_SSL_MODE: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"] = (
        "prefer"
    )

    # ─── Redis ─────────────────────────────────────────────
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""

    # ─── OAuth / SSO ───────────────────────────────────────
    OAUTH_GOOGLE_CLIENT_ID: str = ""
    OAUTH_GOOGLE_CLIENT_SECRET: str = ""
    OAUTH_GITHUB_CLIENT_ID: str = ""
    OAUTH_GITHUB_CLIENT_SECRET: str = ""
    OAUTH_MICROSOFT_CLIENT_ID: str = ""
    OAUTH_MICROSOFT_CLIENT_SECRET: str = ""
    # Absolute URL the IdP should redirect to after consent. Defaults derived
    # from CORS_ALLOW_ORIGINS when left blank; the frontend page at
    # ``/oauth/callback`` owns the subsequent token exchange.
    OAUTH_REDIRECT_BASE: str = ""
    OIDC_ISSUER: str = ""
    OIDC_CLIENT_ID: str = ""
    OIDC_CLIENT_SECRET: str = ""

    # ─── LLM / search providers ────────────────────────────
    # Business-level credentials are NEVER read from .env. Operators configure
    # them via Settings → Providers / Search providers, which envelope-encrypt
    # the keys via Vault. The startup hook in ``main.lifespan`` warns when the
    # process env still contains legacy ``*_API_KEY`` variables.
    LEGACY_ENV_KEYS: ClassVar[tuple[str, ...]] = (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "OPENROUTER_API_KEY",
        "DEEPSEEK_API_KEY",
        "MOONSHOT_API_KEY",
        "MOONSHOTAI_API_KEY",
        "GROQ_API_KEY",
        "OLLAMA_HOST",
        "OLLAMA_MODEL",
        "LLM_DEFAULT_PROVIDER",
        "TAVILY_API_KEY",
        "SERPAPI_KEY",
        "BRAVE_SEARCH_API_KEY",
        "JINA_API_KEY",
        "EXA_API_KEY",
    )

    # ─── Observability ─────────────────────────────────────
    LOGFIRE_TOKEN: str = ""
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""
    SENTRY_DSN: str = ""
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = ""  # default: https://cloud.langfuse.com
    LOG_FORMAT: Literal["text", "json"] = "text"
    # Optional rotating file sink. Blank keeps logs stdout-only.
    # When set (typically to ``${STORAGE_LOCAL_PATH}/logs``) the root
    # logger also writes ``backend.log`` rotated per the two caps below.
    LOG_DIR: str = ""
    LOG_FILE_MAX_BYTES: int = 10 * 1024 * 1024
    LOG_FILE_BACKUPS: int = 5

    # ─── Pricing ───────────────────────────────────────────
    # Optional JSON override of the model pricing catalog.
    # Shape: {"<model>": [input_usd_per_mtok, output_usd_per_mtok], ...}
    PRICING_OVERRIDES_JSON: str = ""

    # ─── Rate limit ────────────────────────────────────────
    RATE_LIMIT_DEFAULT_LIMIT: int = 120
    RATE_LIMIT_DEFAULT_PERIOD: int = 60
    # Auth-specific limits — raise these in dev/CI to allow e2e test suites
    # that register many users in quick succession.
    AUTH_REGISTER_RATE_LIMIT: int = 3
    AUTH_REGISTER_RATE_PERIOD: int = 60
    AUTH_LOGIN_RATE_LIMIT: int = 5
    AUTH_LOGIN_RATE_PERIOD: int = 60

    # ─── Storage ───────────────────────────────────────────
    STORAGE_BACKEND: Literal["local", "s3", "oss"] = "local"
    STORAGE_LOCAL_PATH: str = "/data/storage"

    # ─── Sandbox ──────────────────────────────────────────
    # In production we refuse ``sandbox.kind=local`` with ``execute=True``
    # because that runs arbitrary shell inside the SenHarness backend process.
    # Set this to ``True`` only after reviewing the security tradeoff
    # (see docs/deployment.md "Agent sandbox" section).
    SANDBOX_LOCAL_EXECUTE_PROD: bool = False

    # ─── OpenClaw gateway (D18) ───────────────────────────
    # How long a pending-request /poll call may block waiting for work before
    # returning an empty batch, and how long an OpenClaw run may take end-to-end
    # before the kernel bails out with a TIMEOUT error.
    OPENCLAW_GATEWAY_POLL_MAX_WAIT_MS: int = 20000
    OPENCLAW_GATEWAY_RUN_TIMEOUT_S: int = 300
    # Internal polling cadence OpenClawBackend.run() uses to drain events.
    OPENCLAW_RUN_POLL_INTERVAL_MS: int = 200

    # ─── Channel runtime (IM stream mode) ─────────────────
    # The IM stream subsystem (Feishu / DingTalk / WeCom / Discord / QQ /
    # WeChat-iLink) opens long-lived outbound connections. When this flag
    # is True (the default), the FastAPI lifespan starts the runtime in
    # the API process — perfect for single-worker / docker-compose dev.
    # In multi-worker production, set to False and run a dedicated worker
    # via ``python -m cli.commands channels run`` so the API tier stays
    # focused on HTTP traffic.
    CHANNEL_RUNTIME_INPROCESS: bool = True
    # When multiple processes run the channel runtime simultaneously, an
    # advisory Redis lock keyed on the channel id ensures only one of them
    # holds the live IM connection. Single-worker deployments can leave
    # this off.
    CHANNEL_RUNTIME_REDIS_LOCK: bool = False
    # Cap on the exponential reconnect backoff after repeated stream
    # failures. The schedule grows 1s → 3s → 8s → 20s → 60s and then
    # stays here.
    CHANNEL_RUNTIME_RECONNECT_BACKOFF_MAX_S: int = 300
    # How often the supervisor checks for new / stopped channels and
    # re-aligns its task set. Also drives the lock TTL refresh cadence.
    CHANNEL_STREAM_HEARTBEAT_S: int = 30
    # Hard ceiling on how long ChannelRuntime.stop_all() will wait for
    # outstanding stream tasks to honour cancellation before orphaning
    # them. Without this cap a single misbehaving provider (e.g. a
    # blocking SDK thread that refuses client.stop()) can stall the
    # whole uvicorn worker shutdown / reload and freeze the API tier
    # for every tenant.
    CHANNEL_RUNTIME_STOP_TIMEOUT_S: float = 5.0
    # Last-resort exit timer armed in lifespan ``finally``. Some channel
    # SDKs (botpy, dingtalk-stream, lark-oapi) spin up non-daemon
    # threads that refuse to die when the event loop closes, leaving
    # the uvicorn worker process pinned in main-thread teardown for up
    # to 5 minutes. That cascades into "the whole tenant cluster is
    # unusable" because the worker can't recycle. After this many
    # seconds past lifespan exit we call ``os._exit(0)`` to release the
    # port unconditionally. Set to ``0`` to disable.
    WORKER_FORCE_EXIT_GRACE_S: float = 5.0
    # Hard cap on uvicorn's own "Waiting for background tasks to complete"
    # phase, which runs *before* lifespan shutdown (so the os._exit timer
    # above can't help there). A single connection task that never
    # observes the peer closing — e.g. a send-only websocket blocked on a
    # queue — would otherwise pin the worker forever and stall ``--reload``.
    # After this many seconds uvicorn cancels the stragglers and proceeds.
    UVICORN_GRACEFUL_SHUTDOWN_TIMEOUT_S: int = 10

    # ─── GC (B2) ──────────────────────────────────────────
    # Retention windows for the nightly garbage collector. Soft-deleted rows
    # younger than ``ATTACHMENT_GC_DAYS`` stay recoverable; older ones get
    # hard-deleted plus their on-disk blob removed. Audit rows beyond
    # ``AUDIT_RETENTION_DAYS`` are purged outright. Set to 0 to disable a
    # specific sweep.
    ATTACHMENT_GC_DAYS: int = 30
    AUDIT_RETENTION_DAYS: int = 365
    APPROVAL_RETENTION_DAYS: int = 90  # only non-pending rows
    KNOWLEDGE_DOC_GC_DAYS: int = 30
    S3_BUCKET: str = ""
    S3_REGION: str = ""
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""

    # ─── Production secret guard ───────────────────────────
    # Default values here exist for a frictionless dev experience — but they
    # must never ship to production. ``check_production_secrets`` raises
    # when the process is APP_ENV=production and any of these are still at
    # their dev defaults or blank. Called from lifespan startup.
    _DEV_DEFAULT_JWT = "dev-only-do-not-use-in-production!!"
    _DEV_DEFAULT_DB_PW = "senharness"

    def check_production_secrets(self) -> list[str]:
        """Return a list of misconfiguration error strings.

        Empty list means we're good. Non-empty means the caller should refuse
        to start in production (see ``main.lifespan``).
        """
        problems: list[str] = []
        if str(self.APP_ENV).lower() != "production":
            return problems

        if not self.JWT_SECRET_KEY or self.JWT_SECRET_KEY == self._DEV_DEFAULT_JWT:
            problems.append(
                "JWT_SECRET_KEY is unset or still at the dev default "
                "'dev-only-do-not-use-in-production!!'. Generate a strong random value "
                "(e.g. `openssl rand -base64 48`) before deploying."
            )
        if not self.DB_PASSWORD or self.DB_PASSWORD == self._DEV_DEFAULT_DB_PW:
            problems.append(
                "DB_PASSWORD is unset or still at the dev default. "
                "Production databases must use a unique generated password."
            )
        if not self.REDIS_PASSWORD:
            problems.append(
                "REDIS_PASSWORD is empty. Production Redis must require "
                "authentication — an unauthenticated Redis reachable from the "
                "app tier is effectively a remote code execution surface."
            )
        if not self.SENHARNESS_MASTER_KEY:
            problems.append(
                "SENHARNESS_MASTER_KEY is empty. The EnvKeyring would "
                "auto-generate a random key on boot which is lost on every "
                "restart — all Vault-encrypted credentials become unreadable. "
                "Generate once (`python -c 'import secrets; "
                "print(secrets.token_urlsafe(48))'`) and persist in your "
                "secret manager."
            )
        return problems

    # ─── Derived ───────────────────────────────────────────
    @computed_field  # type: ignore[misc]
    @property
    def async_database_url(self) -> str:
        host = self._runtime_db_host()
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{host}:{self.DB_PORT}/{self.DB_NAME}"
        )

    @computed_field  # type: ignore[misc]
    @property
    def alembic_url(self) -> str:
        # Alembic uses the same async URL here (env.py runs it with async engine).
        return self.async_database_url

    def _runtime_db_host(self) -> str:
        """Resolve DB host for host-run development.

        In host mode (Windows/macOS/Linux), the compose service alias ``db``
        usually does not resolve, while ``localhost`` does via published ports.
        Inside Docker networks, ``db`` resolves correctly, so no fallback fires.
        """
        host = (self.DB_HOST or "").strip() or "localhost"
        if str(self.APP_ENV).lower() == "production":
            return host
        if host.lower() not in {"db", "postgres", "postgresql"}:
            return host
        try:
            socket.getaddrinfo(host, self.DB_PORT)
            return host
        except OSError:
            return "localhost"

    @computed_field  # type: ignore[misc]
    @property
    def redis_url(self) -> str:
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @computed_field  # type: ignore[misc]
    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ALLOW_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings: Settings = get_settings()
