"""Upload, extraction preview, and confirmation.

Nothing an adapter produces reaches the patient timeline on its own. The flow
is upload, extract, show the physician what was read next to what it was read
from, then commit on confirmation. An OCR pipeline that writes straight into a
clinical record is a liability.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.engine.vision_client import VisionUnavailable
from app.schemas import (
    Envelope,
    ExtractionPreview,
    JobState,
    JobStatus,
    Meta,
    NormalizedScan,
    UploadAccepted,
)
from app.storage import StorageUnavailable, put_pdf
from app.tasks.ocr_job import celery_app, extract_document, run_pipeline

logger = logging.getLogger("boensity.ingest.upload")

router = APIRouter(tags=["ingest"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
PDF_MAGIC = b"%PDF-"


def _meta(**extra) -> Meta:
    return Meta(generated_at=datetime.now(UTC), **extra)


async def _read_and_validate(file: UploadFile) -> bytes:
    content = await file.read()

    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "uploaded file is empty")

    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"file is {len(content) // 1024 // 1024} MB, limit is {MAX_UPLOAD_BYTES // 1024 // 1024} MB",
        )

    # Checked by content, not by extension or the client's content-type header.
    # Both are trivially wrong and neither is a security control.
    if not content.startswith(PDF_MAGIC):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "file is not a PDF")

    return content


@router.post("/upload", response_model=Envelope[UploadAccepted])
async def upload(
    file: UploadFile = File(...),
    patient_id: str | None = Form(default=None),
    sex: str | None = Form(default=None),
) -> Envelope[UploadAccepted]:
    """Store the PDF and queue extraction."""
    content = await _read_and_validate(file)
    document_id = uuid.uuid4().hex

    try:
        stored = put_pdf(document_id, file.filename or "scan.pdf", content)
    except StorageUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    task = extract_document.delay(stored.key, document_id, patient_id, sex)

    return Envelope(
        data=UploadAccepted(
            document_id=document_id,
            job_id=task.id,
            state=JobState.QUEUED,
            filename=file.filename or "scan.pdf",
            size_bytes=stored.size_bytes,
            storage_key=stored.key,
        ),
        error=None,
        meta=_meta(sha256=stored.sha256),
    )


@router.get("/jobs/{job_id}", response_model=Envelope[JobStatus])
async def job_status(job_id: str) -> Envelope[JobStatus]:
    result = celery_app.AsyncResult(job_id)

    if result.state == "PENDING":
        return Envelope(
            data=JobStatus(job_id=job_id, document_id="", state=JobState.QUEUED, progress=0.0),
            error=None,
            meta=_meta(),
        )

    if result.state == "PROGRESS":
        info = result.info or {}
        return Envelope(
            data=JobStatus(
                job_id=job_id,
                document_id=info.get("document_id", ""),
                state=JobState(info.get("state", JobState.EXTRACTING.value)),
                progress=float(info.get("progress", 0.5)),
            ),
            error=None,
            meta=_meta(),
        )

    if result.state == "FAILURE":
        return Envelope(
            data=JobStatus(
                job_id=job_id,
                document_id="",
                state=JobState.FAILED,
                progress=1.0,
                error="extraction failed",
            ),
            error=None,
            meta=_meta(),
        )

    payload = result.result or {}
    scan = payload.get("scan")
    return Envelope(
        data=JobStatus(
            job_id=job_id,
            document_id=payload.get("document_id", ""),
            state=JobState(payload.get("state", JobState.COMPLETE.value)),
            progress=1.0,
            result=NormalizedScan.model_validate(scan) if scan else None,
            error=payload.get("error"),
        ),
        error=None,
        meta=_meta(storage_key=payload.get("storage_key")),
    )


@router.post("/extract", response_model=Envelope[ExtractionPreview])
async def extract_now(
    file: UploadFile = File(...),
    patient_id: str | None = Form(default=None),
    sex: str | None = Form(default=None),
) -> Envelope[ExtractionPreview]:
    """Run extraction inline and return the preview.

    Used by the review screen when a clinician is sitting there waiting. A
    text-native PDF comes back in well under a second, which is faster than
    the round trip through the queue would be.
    """
    content = await _read_and_validate(file)
    document_id = uuid.uuid4().hex

    try:
        result = run_pipeline(content, document_id, patient_id, sex)
    except VisionUnavailable as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"this PDF has no text layer and OCR is unavailable: {exc}",
        ) from exc

    scan = NormalizedScan.model_validate(result["scan"])

    return Envelope(
        data=ExtractionPreview(
            document_id=document_id,
            scan=scan,
            raw=result["raw"],
            confirmable=not scan.requires_review,
        ),
        error=None,
        meta=_meta(extraction_source=scan.extraction_source.value),
    )


@router.post("/confirm", response_model=Envelope[dict])
async def confirm(scan: NormalizedScan) -> Envelope[dict]:
    """Commit a reviewed scan to the Timeline service.

    Accepts the edited payload rather than a document id, because the whole
    point of the review step is that a human may have corrected a value the
    extractor got wrong.
    """
    import os

    timeline_url = os.getenv("TIMELINE_API_URL", "http://timeline:8002")

    if not scan.patient_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "patient_id is required before a scan can be committed",
        )

    rows = [
        {
            "scan_date": scan.scan_date.isoformat(),
            "t_score": measurement.t_score,
            "bmd_g_cm2": measurement.bmd_g_cm2,
            "z_score": measurement.z_score,
            "site": measurement.site.value,
            "manufacturer": scan.manufacturer.value,
            "scanner_model": scan.scanner_model,
            "source_document_id": scan.document_id,
        }
        for measurement in scan.measurements
    ]

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{timeline_url}/scans",
                json={"patient_id": scan.patient_id, "scans": rows},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"could not reach the timeline service: {exc}"
        ) from exc

    return Envelope(
        data={
            "document_id": scan.document_id,
            "patient_id": scan.patient_id,
            "committed_measurements": len(rows),
        },
        error=None,
        meta=_meta(),
    )
