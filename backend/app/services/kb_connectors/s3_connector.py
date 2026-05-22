"""S3 connector — iterate objects under a bucket prefix.

``config`` schema::

    {
      "bucket": "my-bucket",
      "prefix": "knowledge/",          # optional, defaults to ""
      "endpoint_url": "https://...",    # optional, for MinIO / R2 / OSS
      "region_name": "us-east-1",       # optional
      "access_key_id": "...",           # optional (falls back to env / IAM role)
      "secret_access_key": "...",       # optional
      "max_objects": 500,               # optional safety cap
      "include_ext": [".txt", ".md"]    # optional extension allowlist
    }

Requires ``boto3`` at runtime. If it's missing we emit a friendly
progress event rather than crashing the whole sync job.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.db.models.knowledge import DocSourceKind
from app.services.kb_connectors.base import (
    ConnectorDocument,
    ConnectorMeta,
    KbConnector,
    SyncProgressEvent,
)


class S3Connector(KbConnector):
    kind = "s3"

    def metadata(self) -> ConnectorMeta:
        return ConnectorMeta(
            display_name="Amazon S3 / MinIO",
            description=(
                "List objects under an S3 bucket prefix and ingest each as a "
                "document. Works with MinIO, Cloudflare R2, and Aliyun OSS via "
                "endpoint_url."
            ),
            config_schema={
                "required": ["bucket"],
                "properties": {
                    "bucket": {"type": "string"},
                    "prefix": {"type": "string"},
                    "endpoint_url": {"type": "string"},
                    "region_name": {"type": "string"},
                    "access_key_id": {"type": "string"},
                    "secret_access_key": {"type": "string", "format": "password"},
                    "max_objects": {"type": "integer", "minimum": 1, "maximum": 10000},
                    "include_ext": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
            supports_incremental=True,
        )

    async def sync(self, *, config: dict) -> AsyncIterator[ConnectorDocument | SyncProgressEvent]:
        try:
            import boto3
            from botocore.config import Config as BotoConfig
        except ImportError:
            yield SyncProgressEvent(
                level="error",
                msg=(
                    "boto3 is not installed; add `boto3` to backend dependencies to ingest from S3."
                ),
                data={"hint": "pip install boto3"},
            )
            return

        bucket = str(config["bucket"])
        prefix = str(config.get("prefix") or "")
        max_objects = int(config.get("max_objects") or 500)
        include_ext = {e.lower() for e in (config.get("include_ext") or []) if e}

        client_kwargs: dict = {
            "service_name": "s3",
            "config": BotoConfig(retries={"max_attempts": 2, "mode": "standard"}),
        }
        if config.get("endpoint_url"):
            client_kwargs["endpoint_url"] = config["endpoint_url"]
        if config.get("region_name"):
            client_kwargs["region_name"] = config["region_name"]
        if config.get("access_key_id") and config.get("secret_access_key"):
            client_kwargs["aws_access_key_id"] = config["access_key_id"]
            client_kwargs["aws_secret_access_key"] = config["secret_access_key"]

        yield SyncProgressEvent(
            level="info",
            msg=f"listing s3://{bucket}/{prefix}",
            data={"bucket": bucket, "prefix": prefix},
        )

        s3 = boto3.client(**client_kwargs)
        paginator = s3.get_paginator("list_objects_v2")
        seen = 0
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                if seen >= max_objects:
                    yield SyncProgressEvent(
                        level="warn",
                        msg=f"stopped at max_objects={max_objects}",
                        data={"max_objects": max_objects},
                    )
                    return
                key = obj.get("Key")
                if not key or key.endswith("/"):
                    continue
                if include_ext:
                    low = key.lower()
                    if not any(low.endswith(ext) for ext in include_ext):
                        continue
                size = int(obj.get("Size") or 0)
                etag = str(obj.get("ETag") or "").strip('"')
                uri = f"s3://{bucket}/{key}"
                try:
                    resp = s3.get_object(Bucket=bucket, Key=key)
                    body = resp["Body"].read()
                except Exception as exc:  # pragma: no cover — transport-level
                    yield SyncProgressEvent(
                        level="error",
                        msg=f"get_object failed for {key}: {exc}",
                        data={"key": key},
                    )
                    continue
                text = _decode_body(body)
                if text is None:
                    yield SyncProgressEvent(
                        level="warn",
                        msg=f"skipped {key} (non-text body)",
                        data={"key": key, "size": size},
                    )
                    continue
                yield ConnectorDocument(
                    title=key.rsplit("/", 1)[-1][:255] or key,
                    source_kind=DocSourceKind.URL,  # keep the raw_text path
                    source_uri=uri,
                    raw_text=text,
                    external_id=etag or uri,
                    metadata={
                        "connector_kind": self.kind,
                        "bucket": bucket,
                        "key": key,
                        "size_bytes": size,
                        "etag": etag,
                    },
                )
                seen += 1
        yield SyncProgressEvent(
            level="info",
            msg=f"done — {seen} object(s) processed",
            data={"count": seen},
        )


def _decode_body(data: bytes) -> str | None:
    """Best-effort UTF-8 decode for small text blobs; bail out for binaries."""
    if not data:
        return ""
    # 2 MB hard cap keeps one sync cheap; bigger files should pre-chunk.
    if len(data) > 2 * 1024 * 1024:
        return None
    # Quick binary sniff — a lot of NULs => likely binary.
    if b"\x00" in data[: min(len(data), 4096)]:
        return None
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:
        return None
