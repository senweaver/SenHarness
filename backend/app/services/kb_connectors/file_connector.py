"""File connector — pull text from existing attachments.

``config`` schema:
    {"attachment_ids": ["<uuid>", "<uuid>", ...]}
    # or a single id for convenience:
    {"attachment_id": "<uuid>"}

We only emit :class:`SyncProgressEvent` from the connector; the service
layer is responsible for opening the attachment rows + running
``knowledge.ingest_attachment`` so the existing extractor + size / mime
guards stay a single code path.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from app.db.models.knowledge import DocSourceKind
from app.services.kb_connectors.base import (
    ConnectorDocument,
    ConnectorMeta,
    KbConnector,
    SyncProgressEvent,
)


class FileConnector(KbConnector):
    kind = "file"

    def metadata(self) -> ConnectorMeta:
        return ConnectorMeta(
            display_name="File upload",
            description=(
                "Ingest one or more previously uploaded attachments (chat uploads "
                "or the workspace file drawer)."
            ),
            config_schema={
                "required": ["attachment_ids"],
                "properties": {
                    "attachment_ids": {
                        "type": "array",
                        "items": {"type": "string", "format": "uuid"},
                        "description": "Attachment UUIDs to ingest.",
                    },
                },
            },
            supports_incremental=False,
        )

    def validate_config(self, config: dict) -> None:
        ids = self._coerce_ids(config)
        if not ids:
            raise ValueError(
                "connector 'file' requires attachment_ids (non-empty list)"
            )

    @staticmethod
    def _coerce_ids(config: dict) -> list[uuid.UUID]:
        raw: list = []
        single = config.get("attachment_id")
        if single:
            raw.append(single)
        raw.extend(config.get("attachment_ids") or [])
        out: list[uuid.UUID] = []
        for r in raw:
            try:
                out.append(uuid.UUID(str(r)))
            except (TypeError, ValueError):
                continue
        return out

    async def sync(
        self, *, config: dict
    ) -> AsyncIterator[ConnectorDocument | SyncProgressEvent]:
        ids = self._coerce_ids(config)
        yield SyncProgressEvent(
            level="info",
            msg=f"preparing {len(ids)} attachment(s)",
            data={"count": len(ids)},
        )
        for att_id in ids:
            # The concrete text extraction happens in the service layer via
            # ``knowledge.ingest_attachment`` (requires DB session + bytes).
            # We still surface one ConnectorDocument per id so the service
            # has a uniform iterator to loop over and counts line up.
            yield ConnectorDocument(
                title=f"attachment://{att_id}",
                source_kind=DocSourceKind.FILE,
                source_uri=f"attachment://{att_id}",
                raw_text=None,
                external_id=str(att_id),
                metadata={
                    "connector_kind": self.kind,
                    "attachment_id": str(att_id),
                },
            )
