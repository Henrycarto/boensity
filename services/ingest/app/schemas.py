"""Contracts for the Boensity Ingest service.

Extraction is probabilistic. Every value pulled off a scanned report carries
its confidence and the literal text it came from, so a physician can always
see what the machine read before trusting what it decided. A number without
provenance is not usable in a clinical record.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ErrorDetail(BaseModel):
    code: str
    message: str
    field: str | None = None


class Meta(BaseModel):
    model_config = ConfigDict(extra="allow")

    service: str = "ingest"
    version: str = "0.1.0"
    generated_at: datetime | None = None


class Envelope(BaseModel, Generic[T]):
    data: T | None = None
    error: ErrorDetail | None = None
    meta: Meta = Field(default_factory=Meta)


# ---------------------------------------------------------------------------
# Enums shared with the Timeline service
# ---------------------------------------------------------------------------


class Manufacturer(str, Enum):
    HOLOGIC = "hologic"
    GE_LUNAR = "ge_lunar"
    UNKNOWN = "unknown"


class SkeletalSite(str, Enum):
    FEMORAL_NECK = "femoral_neck"
    TOTAL_HIP = "total_hip"
    LUMBAR_SPINE = "lumbar_spine"
    FOREARM = "forearm"


class ExtractionSource(str, Enum):
    """Which reader produced a value.

    pdfplumber on a text-native PDF is close to exact. Cloud Vision OCR on a
    scanned fax is not. The downstream confidence weighting depends on knowing
    which one ran.
    """

    PDF_TEXT_LAYER = "pdf_text_layer"
    CLOUD_VISION_OCR = "cloud_vision_ocr"
    DERIVED = "derived"


class JobState(str, Enum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    NORMALIZING = "normalizing"
    NEEDS_REVIEW = "needs_review"
    COMPLETE = "complete"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Extraction primitives
# ---------------------------------------------------------------------------


class ExtractedValue(BaseModel):
    """One field lifted off the report, with the evidence behind it."""

    value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    source: ExtractionSource
    raw_text: str | None = None
    page: int | None = None

    def is_confident(self, floor: float = 0.75) -> bool:
        return self.confidence >= floor


class SiteMeasurement(BaseModel):
    """A single region of interest as the vendor reported it."""

    site: SkeletalSite
    region_label: str
    bmd_g_cm2: ExtractedValue | None = None
    t_score: ExtractedValue | None = None
    z_score: ExtractedValue | None = None


class RawExtraction(BaseModel):
    """What the reader saw, before any cross-vendor normalisation."""

    manufacturer: Manufacturer
    scanner_model: ExtractedValue | None = None
    scan_date: ExtractedValue | None = None
    patient_reference: ExtractedValue | None = None
    measurements: list[SiteMeasurement] = Field(default_factory=list)
    source: ExtractionSource
    page_count: int = 1
    detection_score: float = Field(default=0.0, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Normalised output
# ---------------------------------------------------------------------------


class NormalizedMeasurement(BaseModel):
    """One site after standardisation, ready for the Timeline hypertable.

    Both the vendor T-score and the recomputed one are kept. The vendor value
    is what is printed on the report the patient may be holding; the
    standardised value is the only one it is valid to plot on a trend line
    that spans more than one scanner.
    """

    site: SkeletalSite
    bmd_g_cm2: float | None = None
    standardized_bmd_mg_cm2: float | None = None
    vendor_t_score: float | None = None
    t_score: float
    z_score: float | None = None
    t_score_is_recomputed: bool
    confidence: float = Field(ge=0.0, le=1.0)
    notes: list[str] = Field(default_factory=list)


class NormalizedScan(BaseModel):
    document_id: str
    patient_id: str | None = None
    patient_reference: str | None = None
    scan_date: date
    manufacturer: Manufacturer
    scanner_model: str | None = None
    measurements: list[NormalizedMeasurement]
    extraction_source: ExtractionSource
    overall_confidence: float = Field(ge=0.0, le=1.0)
    requires_review: bool
    review_reasons: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# API payloads
# ---------------------------------------------------------------------------


class UploadAccepted(BaseModel):
    document_id: str
    job_id: str
    state: JobState
    filename: str
    size_bytes: int
    storage_key: str | None = None


class JobStatus(BaseModel):
    job_id: str
    document_id: str
    state: JobState
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    result: NormalizedScan | None = None
    error: str | None = None


class ExtractionPreview(BaseModel):
    """What the review screen renders before anything is committed.

    Nothing reaches the patient timeline until a human confirms this payload.
    Automated ingest that writes straight into a clinical record is a
    liability, not a feature.
    """

    document_id: str
    scan: NormalizedScan
    raw: RawExtraction
    confirmable: bool
