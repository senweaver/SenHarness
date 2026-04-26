"""Application settings loaded from environment (pydantic-settings)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "staging", "production"]
KeyringProviderKind = Literal[
    "env", "file", "passphrase", "aws_kms", "gcp_kms", "azure_kv", "vault", "hsm"
]


class Settings(BaseSettings):
    """Top-level settings for SenHarness backend."""

    model_config = SettingsConfigDict(
        env_file=(".env",),
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

    JWT_SECRET_KEY: str = "change-me-in-production"
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

    # ─── LLM provider defaults (optional; workspace-level overrides in Vault) ──
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GOOGLE_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""

    # ─── Search providers ──────────────────────────────────
    TAVILY_API_KEY: str = ""
    SERPAPI_KEY: str = ""
    JINA_API_KEY: str = ""
    EXA_API_KEY: str = ""

    # ─── Observability ─────────────────────────────────────
    LOGFIRE_TOKEN: str = ""
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""
    SENTRY_DSN: str = ""
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = ""  # default: https://cloud.langfuse.com
    LOG_FORMAT: Literal["text", "json"] = "text"

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
    _DEV_DEFAULT_JWT = "change-me-in-production"
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
                "'change-me-in-production'. Generate a strong random value "
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
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    @computed_field  # type: ignore[misc]
    @property
    def alembic_url(self) -> str:
        # Alembic uses the same async URL here (env.py runs it with async engine).
        return self.async_database_url

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
