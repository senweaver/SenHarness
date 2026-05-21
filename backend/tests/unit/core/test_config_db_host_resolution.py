"""DB host resolution tests for host-run development."""

from __future__ import annotations

from app.core.config import Settings


def _settings(**overrides) -> Settings:
    base = {
        "APP_ENV": "development",
        "DB_HOST": "db",
        "DB_PORT": 5432,
        "DB_USER": "senharness",
        "DB_PASSWORD": "senharness",
        "DB_NAME": "senharness",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


class TestDbHostResolution:
    def test_dev_falls_back_to_localhost_when_db_alias_unresolvable(self, monkeypatch):
        monkeypatch.setattr("socket.getaddrinfo", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("no dns")))
        s = _settings(APP_ENV="development", DB_HOST="db")
        assert "@localhost:5432/" in s.async_database_url

    def test_dev_keeps_db_alias_when_resolvable(self, monkeypatch):
        monkeypatch.setattr("socket.getaddrinfo", lambda *_args, **_kwargs: [("ok",)])
        s = _settings(APP_ENV="development", DB_HOST="db")
        assert "@db:5432/" in s.async_database_url

    def test_production_never_fallbacks(self, monkeypatch):
        monkeypatch.setattr("socket.getaddrinfo", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("no dns")))
        s = _settings(APP_ENV="production", DB_HOST="db")
        assert "@db:5432/" in s.async_database_url

    def test_non_alias_host_kept_as_is(self, monkeypatch):
        monkeypatch.setattr("socket.getaddrinfo", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("no dns")))
        s = _settings(DB_HOST="127.0.0.1")
        assert "@127.0.0.1:5432/" in s.async_database_url
