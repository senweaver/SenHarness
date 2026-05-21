"""Base abstractions for knowledge base connectors.

A :class:`KbConnector` is a *stateless* translator: given a :class:`KbSource`
config it yields :class:`ConnectorDocument` objects which the service layer
then feeds into the existing ``knowledge.ingest_document`` pipeline. Every
built-in connector lives under :mod:`app.services.kb_connectors` and
registers itself on import, exactly like :mod:`app.services.channels`.

Forks / enterprise modules can register extra connectors at runtime:

    from app.services.kb_connectors import register_connector
    from app.services.kb_connectors.base import KbConnector, ConnectorMeta

    class ConfluenceConnector(KbConnector):
        kind = "confluence"
        ...

    register_connector(ConfluenceConnector())
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from app.db.models.knowledge import DocSourceKind


@dataclass
class ConnectorMeta:
    """Self-description returned by every connector for the connector catalog."""

    display_name: str
    description: str
    config_schema: dict = field(default_factory=dict)
    supports_incremental: bool = False


@dataclass
class ConnectorDocument:
    """A single doc produced by a connector, ready for the ingest pipeline.

    The service layer maps this onto ``KnowledgeDoc`` rows; ``external_id``
    lets incremental connectors skip unchanged docs on the next sync.
    """

    title: str
    source_kind: DocSourceKind
    source_uri: str | None
    raw_text: str | None
    external_id: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class SyncProgressEvent:
    """Progress event emitted by a connector during ``sync()``.

    ``level`` is one of ``info`` / ``warn`` / ``error``; ``data`` is free-form
    JSON that the UI can render (current doc title, counters, ...). The
    service layer forwards these over SSE verbatim.
    """

    level: str
    msg: str
    data: dict = field(default_factory=dict)


class KbConnector(abc.ABC):
    """Abstract connector. Subclasses implement :meth:`sync`."""

    kind: str = ""

    @abc.abstractmethod
    def metadata(self) -> ConnectorMeta:
        """Return human-readable metadata + config schema for the connector catalog."""

    def validate_config(self, config: dict) -> None:
        """Validate ``config`` against :attr:`metadata().config_schema`.

        Default: require every ``required_config_fields`` key. Connectors
        with richer constraints can override this.
        """
        schema = self.metadata().config_schema
        required = set(schema.get("required", []))
        missing = [k for k in required if not config.get(k)]
        if missing:
            raise ValueError(
                f"connector {self.kind!r} missing required config fields: {missing}"
            )

    @abc.abstractmethod
    def sync(
        self, *, config: dict
    ) -> AsyncIterator[ConnectorDocument | SyncProgressEvent]:
        """Yield docs + progress events for one sync pass.

        Implementations are async iterators; the service layer consumes the
        stream sequentially to keep memory bounded even for big connectors
        (large S3 buckets, multi-page wikis, ...).
        """
