"""Unit tests for the KB connector registry + built-in connectors."""

from __future__ import annotations

import pytest

from app.db.models.knowledge import DocSourceKind
from app.services.kb_connectors import (
    ConnectorDocument,
    ConnectorMeta,
    KbConnector,
    SyncProgressEvent,
    available_kinds,
    describe_connectors,
    get_connector,
    register_connector,
)
from app.services.kb_connectors.base import KbConnector as BaseKbConnector
from app.services.kb_connectors.file_connector import FileConnector
from app.services.kb_connectors.url_connector import UrlConnector


class TestRegistry:
    def test_bundled_connectors_registered(self):
        kinds = available_kinds()
        assert {"url", "file", "s3"}.issubset(set(kinds))

    def test_get_connector_unknown_raises(self):
        with pytest.raises(KeyError):
            get_connector("does_not_exist")

    def test_describe_connectors_shape(self):
        rows = describe_connectors()
        assert all({"kind", "display_name", "config_schema"}.issubset(r) for r in rows)
        url_row = next(r for r in rows if r["kind"] == "url")
        assert "url" in url_row["config_schema"]["properties"]

    def test_register_custom_connector(self):
        class _Dummy(BaseKbConnector):
            kind = "dummy_for_test"

            def metadata(self) -> ConnectorMeta:
                return ConnectorMeta(display_name="Dummy", description="x")

            async def sync(self, *, config):
                if False:
                    yield  # make this an async iterator without producing values

        register_connector(_Dummy())
        try:
            assert get_connector("dummy_for_test").kind == "dummy_for_test"
            assert "dummy_for_test" in available_kinds()
        finally:
            from app.services.kb_connectors import _REGISTRY

            _REGISTRY.pop("dummy_for_test", None)

    def test_cannot_register_empty_kind(self):
        class _Bad(KbConnector):
            kind = ""

            def metadata(self) -> ConnectorMeta:
                return ConnectorMeta(display_name="bad", description="x")

            async def sync(self, *, config):
                if False:
                    yield

        with pytest.raises(ValueError):
            register_connector(_Bad())


class TestUrlConnector:
    def test_metadata_schema(self):
        meta = UrlConnector().metadata()
        assert "url" in meta.config_schema["properties"]
        assert "url" in meta.config_schema["required"]

    def test_validate_rejects_unsafe_url(self):
        c = UrlConnector()
        with pytest.raises(ValueError):
            c.validate_config({"url": "http://169.254.169.254/latest/meta-data/"})

    def test_validate_accepts_public_url(self):
        UrlConnector().validate_config({"url": "https://example.com/article"})

    async def test_sync_emits_progress_and_doc(self):
        c = UrlConnector()
        out = []
        async for item in c.sync(config={"url": "https://example.com/x"}):
            out.append(item)
        assert any(isinstance(i, SyncProgressEvent) for i in out)
        docs = [i for i in out if isinstance(i, ConnectorDocument)]
        assert len(docs) == 1
        assert docs[0].source_kind == DocSourceKind.URL
        assert docs[0].source_uri == "https://example.com/x"


class TestFileConnector:
    def test_requires_ids(self):
        with pytest.raises(ValueError):
            FileConnector().validate_config({"attachment_ids": []})

    def test_accepts_single_attachment_id(self):
        import uuid

        FileConnector().validate_config({"attachment_id": str(uuid.uuid4())})

    async def test_sync_emits_one_doc_per_id(self):
        import uuid

        ids = [str(uuid.uuid4()) for _ in range(3)]
        out = []
        async for item in FileConnector().sync(config={"attachment_ids": ids}):
            out.append(item)
        docs = [i for i in out if isinstance(i, ConnectorDocument)]
        assert len(docs) == 3
        assert all(d.source_kind == DocSourceKind.FILE for d in docs)


class TestS3Connector:
    async def test_sync_reports_missing_boto3_gracefully(self, monkeypatch):
        # Force the ImportError path by replacing the import hook.
        import builtins

        from app.services.kb_connectors.s3_connector import S3Connector

        real_import = builtins.__import__

        def _fake(name, *args, **kwargs):
            if name == "boto3":
                raise ImportError("boto3 not installed in tests")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake)

        events = []
        async for item in S3Connector().sync(config={"bucket": "x"}):
            events.append(item)
        assert events, "connector should yield a friendly error event, not raise"
        assert isinstance(events[0], SyncProgressEvent)
        assert events[0].level == "error"
        assert "boto3" in events[0].msg
