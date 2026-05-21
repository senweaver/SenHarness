"""Knowledge-base connector plugin registry.

Mirrors the channel-provider registry so registering a third-party
connector is a one-import, one-call affair — the DB ``kind`` column is a
free-form ``String(32)`` so no migration is needed when a fork ships an
extra connector.

Adding a community connector::

    from app.services.kb_connectors.base import KbConnector, ConnectorMeta
    from app.services.kb_connectors import register_connector

    class ConfluenceConnector(KbConnector):
        kind = "confluence"
        ...

    register_connector(ConfluenceConnector())

Then import the module once from app startup (or your own package
``__init__``) so ``register_connector`` fires.
"""

from __future__ import annotations

import logging

from app.services.kb_connectors.base import (
    ConnectorDocument,
    ConnectorMeta,
    KbConnector,
    SyncProgressEvent,
)

log = logging.getLogger(__name__)

_REGISTRY: dict[str, KbConnector] = {}


def register_connector(connector: KbConnector) -> KbConnector:
    """Register ``connector`` under ``connector.kind`` (last-writer-wins)."""
    if not connector.kind:
        raise ValueError("KbConnector.kind must be a non-empty string")
    if connector.kind in _REGISTRY:
        log.warning(
            "kb connector %r re-registered; last writer wins", connector.kind
        )
    _REGISTRY[connector.kind] = connector
    return connector


def get_connector(kind: str) -> KbConnector:
    """Lookup a connector by kind. Raises ``KeyError`` if not registered."""
    c = _REGISTRY.get(kind)
    if c is None:
        raise KeyError(f"unknown kb connector kind: {kind}")
    return c


def available_kinds() -> list[str]:
    return sorted(_REGISTRY.keys())


def describe_connectors() -> list[dict]:
    """Catalog metadata consumed by ``GET /api/v1/kb/connectors``."""
    out: list[dict] = []
    for kind in sorted(_REGISTRY.keys()):
        c = _REGISTRY[kind]
        meta = c.metadata()
        out.append(
            {
                "kind": kind,
                "display_name": meta.display_name,
                "description": meta.description,
                "config_schema": meta.config_schema,
                "supports_incremental": meta.supports_incremental,
            }
        )
    return out


# ── Bundled connectors — importing them runs register_connector(). ──
from app.services.kb_connectors.file_connector import FileConnector  # noqa: E402
from app.services.kb_connectors.s3_connector import S3Connector  # noqa: E402
from app.services.kb_connectors.url_connector import UrlConnector  # noqa: E402

register_connector(UrlConnector())
register_connector(FileConnector())
register_connector(S3Connector())


__all__ = [
    "ConnectorDocument",
    "ConnectorMeta",
    "KbConnector",
    "SyncProgressEvent",
    "available_kinds",
    "describe_connectors",
    "get_connector",
    "register_connector",
]
