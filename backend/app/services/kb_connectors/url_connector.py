"""URL connector — fetch a single page via trafilatura-aware pipeline.

``config`` schema:
    {"url": "https://...", "title": "optional display title"}
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.core.url_safety import UnsafeURLError, assert_safe_url
from app.db.models.knowledge import DocSourceKind
from app.services.kb_connectors.base import (
    ConnectorDocument,
    ConnectorMeta,
    KbConnector,
    SyncProgressEvent,
)


class UrlConnector(KbConnector):
    kind = "url"

    def metadata(self) -> ConnectorMeta:
        return ConnectorMeta(
            display_name="URL",
            description="Fetch a single web page and chunk it as a document.",
            config_schema={
                "required": ["url"],
                "properties": {
                    "url": {
                        "type": "string",
                        "format": "uri",
                        "description": "Public http(s) URL to fetch.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Display title (defaults to the URL).",
                    },
                },
            },
            supports_incremental=False,
        )

    def validate_config(self, config: dict) -> None:
        super().validate_config(config)
        try:
            assert_safe_url(str(config.get("url", "")))
        except UnsafeURLError as exc:
            raise ValueError(f"unsafe_url: {exc}") from exc

    async def sync(
        self, *, config: dict
    ) -> AsyncIterator[ConnectorDocument | SyncProgressEvent]:
        url = str(config["url"]).strip()
        title = str(config.get("title") or url)[:255]
        yield SyncProgressEvent(level="info", msg=f"fetching {url}", data={"url": url})
        yield ConnectorDocument(
            title=title,
            source_kind=DocSourceKind.URL,
            source_uri=url,
            raw_text=None,
            external_id=url,
            metadata={"connector_kind": self.kind, "source_url": url},
        )
