"""S3 storage for raw uploads.

Raw PDFs are kept because extraction is a model, and models get revised. When
an adapter improves, every document can be reprocessed against the original
rather than being stuck with whatever the first pass produced.

They are also the audit trail. If a physician disputes a value on a chart, the
answer has to be the source document, not a database row.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import date

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger("boensity.ingest.storage")


class StorageUnavailable(RuntimeError):
    pass


@dataclass
class StoredObject:
    bucket: str
    key: str
    size_bytes: int
    sha256: str


def _client():
    endpoint = os.getenv("S3_ENDPOINT_URL") or None
    return boto3.client(
        "s3",
        region_name=os.getenv("AWS_REGION", "eu-central-1"),
        endpoint_url=endpoint,
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
            # MinIO in local dev does not support virtual host addressing.
            s3={"addressing_style": "path"} if endpoint else {},
        ),
    )


def bucket_name() -> str:
    return os.getenv("S3_UPLOAD_BUCKET", "boensity-raw-uploads")


def build_key(document_id: str, filename: str, uploaded_on: date | None = None) -> str:
    """Date-prefixed key so lifecycle rules can act on age without a database read."""
    stamp = (uploaded_on or date.today()).strftime("%Y/%m/%d")
    safe = os.path.basename(filename).replace(" ", "_")[-80:]
    return f"raw/{stamp}/{document_id}/{safe}"


def put_pdf(document_id: str, filename: str, content: bytes) -> StoredObject:
    digest = hashlib.sha256(content).hexdigest()
    key = build_key(document_id, filename)

    try:
        _client().put_object(
            Bucket=bucket_name(),
            Key=key,
            Body=content,
            ContentType="application/pdf",
            # Patient documents are encrypted at rest without exception.
            ServerSideEncryption="AES256",
            Metadata={"document-id": document_id, "sha256": digest},
        )
    except (BotoCoreError, ClientError) as exc:
        raise StorageUnavailable(f"could not store upload: {exc}") from exc

    return StoredObject(bucket=bucket_name(), key=key, size_bytes=len(content), sha256=digest)


def get_pdf(key: str) -> bytes:
    try:
        response = _client().get_object(Bucket=bucket_name(), Key=key)
        return response["Body"].read()
    except (BotoCoreError, ClientError) as exc:
        raise StorageUnavailable(f"could not read {key}: {exc}") from exc


def presigned_url(key: str, expires_seconds: int = 900) -> str:
    """Short-lived link used by the review screen to show the source page.

    Fifteen minutes because these URLs end up in browser history and server
    logs, and a link to a patient document should stop working quickly.
    """
    try:
        return _client().generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket_name(), "Key": key},
            ExpiresIn=expires_seconds,
        )
    except (BotoCoreError, ClientError) as exc:
        raise StorageUnavailable(f"could not sign url for {key}: {exc}") from exc
