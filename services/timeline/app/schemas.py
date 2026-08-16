"""Request and response contracts for the Boensity Timeline service.

Every route returns an Envelope so the frontend has exactly one response shape
to branch on. Clinical values carry their measurement site because a T-score is
meaningless without knowing where on the skeleton it was measured.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


class ErrorDetail(BaseModel):
    code: str
    message: str
    field: str | None = None


class Meta(BaseModel):
    model_config = ConfigDict(extra="allow")

    service: str = "timeline"
    version: str = "0.1.0"
    generated_at: datetime | None = None


class Envelope(BaseModel, Generic[T]):
    """The single response shape: {data, error, meta}."""

    data: T | None = None
    error: ErrorDetail | None = None
    meta: Meta = Field(default_factory=Meta)


# ---------------------------------------------------------------------------
# Clinical enums
# ---------------------------------------------------------------------------


class Sex(str, Enum):
    FEMALE = "female"
    MALE = "male"


class SkeletalSite(str, Enum):
    """Sites Boensity models. FRAX is anchored to femoral neck by definition,
    so any BMD-adjusted FRAX call requires that site specifically."""

    FEMORAL_NECK = "femoral_neck"
    TOTAL_HIP = "total_hip"
    LUMBAR_SPINE = "lumbar_spine"
    FOREARM = "forearm"


class Manufacturer(str, Enum):
    HOLOGIC = "hologic"
    GE_LUNAR = "ge_lunar"
    UNKNOWN = "unknown"


class DiagnosticBand(str, Enum):
    """WHO 1994 densitometric categories."""

    NORMAL = "normal"
    OSTEOPENIA = "osteopenia"
    OSTEOPOROSIS = "osteoporosis"


class TrendDirection(str, Enum):
    DECLINING = "declining"
    STABLE = "stable"
    IMPROVING = "improving"


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Scan observations
# ---------------------------------------------------------------------------


class ScanObservation(BaseModel):
    """One DEXA measurement at one skeletal site.

    bmd_g_cm2 is kept alongside the T-score because cross-manufacturer
    comparison is only valid on standardised BMD, never on the vendor T-score.
    """

    scan_date: date
    t_score: float = Field(ge=-8.0, le=6.0)
    bmd_g_cm2: float | None = Field(default=None, gt=0.0, lt=3.0)
    z_score: float | None = Field(default=None, ge=-8.0, le=6.0)
    site: SkeletalSite = SkeletalSite.FEMORAL_NECK
    manufacturer: Manufacturer = Manufacturer.UNKNOWN
    scanner_model: str | None = None
    source_document_id: str | None = None

    model_config = ConfigDict(use_enum_values=False)


class PatientContext(BaseModel):
    """FRAX clinical risk factors. Field names mirror the published FRAX
    input variables so a clinician reading the payload recognises them."""

    patient_id: str
    age: float = Field(ge=40.0, le=90.0, description="FRAX is validated 40 to 90")
    sex: Sex
    weight_kg: float = Field(gt=20.0, lt=300.0)
    height_cm: float = Field(gt=100.0, lt=250.0)

    previous_fracture: bool = False
    parent_hip_fracture: bool = False
    current_smoking: bool = False
    glucocorticoids: bool = False
    rheumatoid_arthritis: bool = False
    secondary_osteoporosis: bool = False
    alcohol_three_or_more_units: bool = False

    @property
    def bmi(self) -> float:
        height_m = self.height_cm / 100.0
        return self.weight_kg / (height_m * height_m)


# ---------------------------------------------------------------------------
# Trajectory
# ---------------------------------------------------------------------------


class TrajectoryRequest(BaseModel):
    patient: PatientContext
    scans: list[ScanObservation] = Field(min_length=1)
    site: SkeletalSite = SkeletalSite.FEMORAL_NECK
    projection_years: int = Field(default=10, ge=1, le=20)

    @field_validator("scans")
    @classmethod
    def _reject_duplicate_dates(cls, scans: list[ScanObservation]) -> list[ScanObservation]:
        seen: set[tuple[date, SkeletalSite]] = set()
        for scan in scans:
            key = (scan.scan_date, scan.site)
            if key in seen:
                raise ValueError(f"duplicate scan for {scan.site.value} on {scan.scan_date.isoformat()}")
            seen.add(key)
        return scans

    @model_validator(mode="after")
    def _require_scans_at_site(self) -> TrajectoryRequest:
        if not any(scan.site == self.site for scan in self.scans):
            raise ValueError(f"no scans supplied for requested site {self.site.value}")
        return self


class TrajectoryPoint(BaseModel):
    """A point on the modelled line. Historical points carry the observed
    value; projected points carry the fitted value plus its interval."""

    date: date
    years_from_baseline: float
    t_score: float
    is_projected: bool
    ci_lower: float | None = None
    ci_upper: float | None = None
    band: DiagnosticBand


class VelocityReport(BaseModel):
    t_score_per_year: float
    bmd_percent_per_year: float | None = None
    standard_error: float | None = None
    ci_lower: float | None = None
    ci_upper: float | None = None
    r_squared: float | None = None
    direction: TrendDirection
    exceeds_least_significant_change: bool
    interpretation: str


class ThresholdCrossing(BaseModel):
    threshold_t_score: float
    band_entered: DiagnosticBand
    crossing_date: date | None
    years_until_crossing: float | None
    already_crossed: bool
    confidence: Literal["high", "moderate", "low"]


class Alert(BaseModel):
    code: str
    severity: AlertSeverity
    title: str
    detail: str


class TrajectoryResponse(BaseModel):
    # model_quality is a clinical term here, not a pydantic namespace clash.
    model_config = ConfigDict(protected_namespaces=())

    patient_id: str
    site: SkeletalSite
    scan_count: int
    observation_span_years: float
    current_t_score: float
    current_band: DiagnosticBand
    points: list[TrajectoryPoint]
    velocity: VelocityReport
    crossings: list[ThresholdCrossing]
    alerts: list[Alert]
    model_quality: str


# ---------------------------------------------------------------------------
# FRAX
# ---------------------------------------------------------------------------


class FraxRequest(BaseModel):
    patient: PatientContext
    femoral_neck_t_score: float | None = Field(default=None, ge=-8.0, le=6.0)
    include_treatment_delta: bool = True
    treatment: Literal["bisphosphonate", "denosumab", "none"] = "bisphosphonate"


class TreatmentDelta(BaseModel):
    agent: str
    major_osteoporotic_percent: float
    hip_percent: float
    absolute_risk_reduction_major: float
    absolute_risk_reduction_hip: float
    number_needed_to_treat_major: int | None
    evidence_note: str


class FraxResponse(BaseModel):
    patient_id: str
    major_osteoporotic_percent: float
    hip_percent: float
    bmd_included: bool
    intervention_threshold_met: bool
    threshold_basis: str
    contributing_factors: list[str]
    treatment: TreatmentDelta | None = None
    calibration_note: str


class ProjectedFraxPoint(BaseModel):
    """FRAX recomputed at a future age against the projected T-score.

    This is the number that makes the trajectory actionable: not what the risk
    is today, but what it becomes if the current decline continues untreated.
    """

    year_offset: int
    age: float
    projected_t_score: float
    major_osteoporotic_percent: float
    hip_percent: float
