"""Celery task that runs the extraction pipeline off the request path.

OCR on a scanned document takes seconds and depends on an external API. Doing
that inside the HTTP handler would tie a browser connection to Google's
latency and lose the work entirely on a timeout. The upload returns a job id
immediately and the frontend polls.
"""

from __future__ import annotations

import asyncio
import logging
import os

from celery import Celery
from celery.utils.log import get_task_logger

from app.engine import pdf_parser, schema_mapper
from app.engine.vision_client import VisionUnavailable, get_vision_client
from app.schemas import ExtractionSource, JobState
from app.storage import StorageUnavailable, get_pdf

logger: logging.Logger = get_task_logger(__name__)

celery_app = Celery(
    "boensity.ingest",
    broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/1"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/2"),
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # A Vision call can take 30 seconds on a dense scan. The soft limit gives
    # the task a chance to record a useful failure before the hard kill.
    task_soft_time_limit=int(os.getenv("OCR_SOFT_TIME_LIMIT", "120")),
    task_time_limit=int(os.getenv("OCR_HARD_TIME_LIMIT", "180")),
    task_acks_late=True,
    # OCR is billed per page. Prefetching a queue into one worker means a crash
    # re-runs documents that were already paid for.
    worker_prefetch_multiplier=1,
    result_expires=int(os.getenv("RESULT_EXPIRY_SECONDS", "86400")),
)


def run_pipeline(
    pdf_bytes: bytes,
    document_id: str,
    patient_id: str | None = None,
    sex: str | None = None,
) -> dict:
    """Text layer first, OCR only if needed, then normalise.

    Shared by the Celery task and the synchronous preview endpoint so the two
    paths cannot drift apart and produce different numbers for one document.
    """
    document = pdf_parser.parse_pdf(pdf_bytes)
    source = ExtractionSource.PDF_TEXT_LAYER
    ocr_confidence: float | None = None

    if not document.has_usable_text_layer:
        client = get_vision_client()
        try:
            ocr = asyncio.run(client.extract_pdf_text(pdf_bytes, document.page_count))
            document = pdf_parser.parsed_from_ocr_text(ocr.text, ocr.page_count)
            source = ExtractionSource.CLOUD_VISION_OCR
            ocr_confidence = ocr.mean_confidence
            logger.info("ocr complete for %s via %s", document_id, ocr.engine)
        except VisionUnavailable as exc:
            logger.warning("vision unavailable for %s: %s", document_id, exc)
            raise

    extraction = schema_mapper.extract_with_best_adapter(document, source, ocr_confidence)
    scan = schema_mapper.normalize(
        extraction,
        document_id=document_id,
        patient_id=patient_id,
        sex=sex,
    )

    return {
        "state": JobState.NEEDS_REVIEW.value if scan.requires_review else JobState.COMPLETE.value,
        "scan": scan.model_dump(mode="json"),
        "raw": extraction.model_dump(mode="json"),
    }


@celery_app.task(
    bind=True,
    name="ingest.extract_document",
    autoretry_for=(StorageUnavailable,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def extract_document(
    self,
    storage_key: str,
    document_id: str,
    patient_id: str | None = None,
    sex: str | None = None,
) -> dict:
    self.update_state(state="PROGRESS", meta={"state": JobState.EXTRACTING.value, "progress": 0.2})

    pdf_bytes = get_pdf(storage_key)

    self.update_state(state="PROGRESS", meta={"state": JobState.NORMALIZING.value, "progress": 0.6})

    try:
        result = run_pipeline(pdf_bytes, document_id, patient_id, sex)
    except VisionUnavailable as exc:
        # Not retried. Missing credentials or an unreadable scan will fail the
        # same way every time, and retrying bills for the same failure twice.
        return {
            "state": JobState.FAILED.value,
            "error": f"text extraction unavailable: {exc}",
            "document_id": document_id,
        }

    result["document_id"] = document_id
    result["storage_key"] = storage_key
    return result
