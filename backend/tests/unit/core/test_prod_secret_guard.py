"""Production secret-guard tests.

The guard exists to make startup fail loudly when operators forget to
replace dev defaults in production. Failing loudly beats silently booting
with a shared-across-the-internet JWT key.
"""

from __future__ import annotations

from app.core.config import Settings


def _settings(**overrides) -> Settings:
    """Build a ``Settings`` instance with explicit values (bypass .env)."""
    base = {
        "APP_ENV": "production",
        "JWT_SECRET_KEY": "a-strong-random-key-please-generate-this-properly",
        "DB_PASSWORD": "a-strong-db-password",
        "REDIS_PASSWORD": "a-strong-redis-password",
        "SENHARNESS_MASTER_KEY": "a-strong-master-key-base64",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


class TestProductionGuard:
    def test_all_good_returns_empty_problems(self):
        s = _settings()
        assert s.check_production_secrets() == []

    def test_development_env_always_passes(self):
        """Dev defaults are fine in development — the guard only bites
        under ``APP_ENV=production``."""
        s = _settings(
            APP_ENV="development",
            JWT_SECRET_KEY="dev-only-do-not-use-in-production!!",
            DB_PASSWORD="senharness",
            REDIS_PASSWORD="",
            SENHARNESS_MASTER_KEY="",
        )
        assert s.check_production_secrets() == []

    def test_default_jwt_caught(self):
        s = _settings(JWT_SECRET_KEY="dev-only-do-not-use-in-production!!")
        problems = s.check_production_secrets()
        assert any("JWT_SECRET_KEY" in p for p in problems)

    def test_empty_jwt_caught(self):
        s = _settings(JWT_SECRET_KEY="")
        problems = s.check_production_secrets()
        assert any("JWT_SECRET_KEY" in p for p in problems)

    def test_default_db_password_caught(self):
        s = _settings(DB_PASSWORD="senharness")
        problems = s.check_production_secrets()
        assert any("DB_PASSWORD" in p for p in problems)

    def test_empty_redis_password_caught(self):
        s = _settings(REDIS_PASSWORD="")
        problems = s.check_production_secrets()
        assert any("REDIS_PASSWORD" in p for p in problems)

    def test_empty_master_key_caught(self):
        s = _settings(SENHARNESS_MASTER_KEY="")
        problems = s.check_production_secrets()
        assert any("SENHARNESS_MASTER_KEY" in p for p in problems)

    def test_multiple_problems_reported_together(self):
        """Operators should see every fix needed in one startup log, not
        one-at-a-time across N restart cycles."""
        s = _settings(
            JWT_SECRET_KEY="dev-only-do-not-use-in-production!!",
            DB_PASSWORD="senharness",
            REDIS_PASSWORD="",
            SENHARNESS_MASTER_KEY="",
        )
        problems = s.check_production_secrets()
        assert len(problems) == 4

    def test_staging_env_not_production(self):
        """Staging is non-production — guard doesn't fire. Operators can
        still deliberately set APP_ENV=production on staging to test
        the guard itself."""
        s = _settings(
            APP_ENV="staging",
            JWT_SECRET_KEY="dev-only-do-not-use-in-production!!",
        )
        assert s.check_production_secrets() == []
