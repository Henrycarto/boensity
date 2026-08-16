"""Google Cloud Vision OCR for scanned DEXA reports.

Used only when pdf_parser finds no usable text layer. See that module for why
the order matters.

Two Vision paths exist for PDFs and they are not interchangeable:

  batch_annotate_files    synchronous, inline bytes, hard limit of 5 pages
  async_batch_annotate    asynchronous, reads from GCS, writes JSON to GCS

DEXA reports are almost always one or two pages, so the synchronous path
handles the normal case and returns in a couple of seconds. The async path
exists for the occasional multi-year archive PDF a clinic exports as one file.
Picking the wrong one either truncates a long document silently or adds GCS
round trips to a job that did not need them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("boensity.ingest.vision")

SYNCHRONOUS_PAGE_LIMIT = 5


class VisionUnavailable(RuntimeError):
    """Raised when Vision is not configured or the call fails.

    Separate from a generic exception so the router can return "this document
    needs manual entry" rather than a 500. A clinic without Vision credentials
    should still be able to use the product with text-native PDFs.
    """


@dataclass
class OcrResult:
    text: str
    page_count: int
    mean_confidence: float
    engine: str


def _credentials_present() -> bool:
    path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    return bool(path and os.path.exists(path))


class VisionClient:
    """Thin async wrapper over the Vision document text detection API.

    The google-cloud-vision SDK is synchronous and releases the GIL during the
    HTTP call, so it is driven through a thread executor rather than blocking
    the event loop. Wrapping it this way keeps the FastAPI handler async
    without pulling in a second HTTP stack to hand-roll the REST calls.
    """

    def __init__(self, gcs_bucket: str | None = None) -> None:
        self._client = None
        self._gcs_bucket = gcs_bucket or os.getenv("GCS_VISION_BUCKET")

    def _ensure_client(self):
        if self._client is not None:
            return self._client

        if not _credentials_present():
            raise VisionUnavailable("GOOGLE_APPLICATION_CREDENTIALS is unset or points at a missing file")

        try:
            from google.cloud import vision
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise VisionUnavailable("google-cloud-vision is not installed") from exc

        self._client = vision.ImageAnnotatorClient()
        return self._client

    @property
    def available(self) -> bool:
        try:
            self._ensure_client()
            return True
        except VisionUnavailable:
            return False

    async def extract_pdf_text(self, pdf_bytes: bytes, page_count: int = 1) -> OcrResult:
        if page_count > SYNCHRONOUS_PAGE_LIMIT:
            return await self._extract_async_path(pdf_bytes, page_count)
        return await asyncio.to_thread(self._extract_sync_path, pdf_bytes)

    # -- synchronous path -------------------------------------------------

    def _extract_sync_path(self, pdf_bytes: bytes) -> OcrResult:
        from google.cloud import vision

        client = self._ensure_client()
        request = vision.AnnotateFileRequest(
            input_config=vision.InputConfig(
                content=pdf_bytes,
                mime_type="application/pdf",
            ),
            features=[vision.Feature(type_=vision.Feature.Type.DOCUMENT_TEXT_DETECTION)],
        )

        try:
            response = client.batch_annotate_files(requests=[request])
        except Exception as exc:
            raise VisionUnavailable(f"Vision batch_annotate_files failed: {exc}") from exc

        pages: list[str] = []
        confidences: list[float] = []

        for file_response in response.responses:
            for page_response in file_response.responses:
                if page_response.error.message:
                    logger.warning("vision page error: %s", page_response.error.message)
                    continue
                annotation = page_response.full_text_annotation
                if annotation.text:
                    pages.append(annotation.text)
                for page in annotation.pages:
                    if page.confidence:
                        confidences.append(float(page.confidence))

        if not pages:
            raise VisionUnavailable("Vision returned no text for this document")

        return OcrResult(
            text="\n".join(pages),
            page_count=len(pages),
            mean_confidence=(sum(confidences) / len(confidences)) if confidences else 0.80,
            engine="cloud_vision_sync",
        )

    # -- asynchronous path ------------------------------------------------

    async def _extract_async_path(self, pdf_bytes: bytes, page_count: int) -> OcrResult:
        """Long documents go through GCS because Vision will not accept them inline."""
        if not self._gcs_bucket:
            raise VisionUnavailable(
                f"document has {page_count} pages, above the {SYNCHRONOUS_PAGE_LIMIT} page "
                "inline limit, and GCS_VISION_BUCKET is not configured"
            )
        return await asyncio.to_thread(self._run_async_annotate, pdf_bytes)

    def _run_async_annotate(self, pdf_bytes: bytes) -> OcrResult:
        import uuid

        from google.cloud import storage, vision

        client = self._ensure_client()
        storage_client = storage.Client()
        bucket = storage_client.bucket(self._gcs_bucket)

        job_id = uuid.uuid4().hex
        source_blob = bucket.blob(f"vision-in/{job_id}.pdf")
        output_prefix = f"vision-out/{job_id}/"

        source_blob.upload_from_string(pdf_bytes, content_type="application/pdf")

        try:
            request = vision.AsyncAnnotateFileRequest(
                input_config=vision.InputConfig(
                    gcs_source=vision.GcsSource(uri=f"gs://{self._gcs_bucket}/{source_blob.name}"),
                    mime_type="application/pdf",
                ),
                features=[vision.Feature(type_=vision.Feature.Type.DOCUMENT_TEXT_DETECTION)],
                output_config=vision.OutputConfig(
                    gcs_destination=vision.GcsDestination(uri=f"gs://{self._gcs_bucket}/{output_prefix}"),
                    batch_size=20,
                ),
            )
            operation = client.async_batch_annotate_files(requests=[request])
            operation.result(timeout=int(os.getenv("VISION_TIMEOUT_SECONDS", "300")))

            pages: list[str] = []
            confidences: list[float] = []
            for blob in storage_client.list_blobs(self._gcs_bucket, prefix=output_prefix):
                if not blob.name.endswith(".json"):
                    continue
                payload = json.loads(blob.download_as_bytes())
                for response in payload.get("responses", []):
                    annotation = response.get("fullTextAnnotation", {})
                    if annotation.get("text"):
                        pages.append(annotation["text"])
                    for page in annotation.get("pages", []):
                        if "confidence" in page:
                            confidences.append(float(page["confidence"]))

            if not pages:
                raise VisionUnavailable("Vision async job produced no text")

            return OcrResult(
                text="\n".join(pages),
                page_count=len(pages),
                mean_confidence=(sum(confidences) / len(confidences)) if confidences else 0.80,
                engine="cloud_vision_async",
            )
        finally:
            # Patient documents must not linger in an intermediate bucket. This
            # runs even when the operation times out or the parse fails.
            try:
                source_blob.delete()
                for blob in storage_client.list_blobs(self._gcs_bucket, prefix=output_prefix):
                    blob.delete()
            except Exception:
                logger.exception("failed to clean up Vision scratch objects for %s", job_id)


_default_client: VisionClient | None = None


def get_vision_client() -> VisionClient:
    global _default_client
    if _default_client is None:
        _default_client = VisionClient()
    return _default_client
