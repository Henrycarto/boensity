"""Scan persistence.

The write side of the hypertable. Ingest calls this once a physician has
confirmed an extraction, and the patient timeline reads back through it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app import db
from app.schemas import Envelope, Meta, ScanObservation, SkeletalSite

router = APIRouter(prefix="/scans", tags=["scans"])


class ScanBatch(BaseModel):
    patient_id: str
    scans: list[ScanObservation] = Field(min_length=1)


@router.post("", response_model=Envelope[dict])
async def store_scans(batch: ScanBatch) -> Envelope[dict]:
    """Write a confirmed scan set.

    Upserts on (patient_id, scan_date, site). Re-uploading the same report is
    a normal thing for a clinic to do, and it must not put a second point on
    the chart.
    """
    try:
        for scan in batch.scans:
            await db.upsert_scan(
                batch.patient_id,
                {
                    "scan_date": scan.scan_date,
                    "site": scan.site.value,
                    "t_score": scan.t_score,
                    "z_score": scan.z_score,
                    "bmd_g_cm2": scan.bmd_g_cm2,
                    "manufacturer": scan.manufacturer.value,
                    "scanner_model": scan.scanner_model,
                    "source_document_id": scan.source_document_id,
                },
            )
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    return Envelope(
        data={"patient_id": batch.patient_id, "stored": len(batch.scans)},
        error=None,
        meta=Meta(generated_at=datetime.now(UTC)),
    )


@router.get("/{patient_id}", response_model=Envelope[list[dict]])
async def list_scans(patient_id: str, site: SkeletalSite = SkeletalSite.FEMORAL_NECK) -> Envelope[list[dict]]:
    try:
        rows = await db.fetch_series(patient_id, site.value)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    return Envelope(
        data=[
            {
                **row,
                "scan_date": row["scan_date"].isoformat() if row.get("scan_date") else None,
            }
            for row in rows
        ],
        error=None,
        meta=Meta(generated_at=datetime.now(UTC), site=site.value, count=len(rows)),
    )


@router.get("", response_model=Envelope[list[dict]])
async def at_risk_cohort(
    site: SkeletalSite = SkeletalSite.FEMORAL_NECK,
    max_velocity: float = -0.05,
    limit: int = 100,
) -> Envelope[list[dict]]:
    """Patients not yet osteoporotic but heading there fastest.

    This is the department screen. Sorted by rate of loss rather than by
    current T-score, because the patients worth finding early are the ones who
    still look acceptable today.
    """
    try:
        rows = await db.fetch_at_risk_cohort(site.value, max_velocity, limit)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    return Envelope(
        data=rows,
        error=None,
        meta=Meta(
            generated_at=datetime.now(UTC),
            site=site.value,
            max_velocity=max_velocity,
            count=len(rows),
        ),
    )
