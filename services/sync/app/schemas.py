"""Contracts for the Boensity Sync service.

Adherence is where bone treatment actually fails. Roughly half of patients
started on an oral bisphosphonate have stopped within a year, and a drug
nobody takes moves no fracture risk at all. The Timeline service can prove a
patient needs treatment. This service tracks whether they are getting it.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

T = TypeVar("T")


class ErrorDetail(BaseModel):
    code: str
    message: str
    field: str | None = None


class Meta(BaseModel):
    model_config = ConfigDict(extra="allow")

    service: str = "sync"
    version: str = "0.1.0"
    generated_at: datetime | None = None


class Envelope(BaseModel, Generic[T]):
    data: T | None = None
    error: ErrorDetail | None = None
    meta: Meta = Field(default_factory=Meta)


class DrugClass(str, Enum):
    """Bone-active agents, split by how adherence behaves for each.

    Dosing interval drives everything downstream. A weekly oral tablet and a
    six monthly injection fail in completely different ways, and a single
    adherence formula applied to both is meaningless.
    """

    ORAL_BISPHOSPHONATE = "oral_bisphosphonate"
    IV_BISPHOSPHONATE = "iv_bisphosphonate"
    DENOSUMAB = "denosumab"
    SERM = "serm"
    ANABOLIC = "anabolic"
    SUPPLEMENT = "supplement"


class AdherenceBand(str, Enum):
    ADHERENT = "adherent"
    PARTIAL = "partial"
    NON_ADHERENT = "non_adherent"
    LAPSED = "lapsed"


class ReferralStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    SUGGESTED = "suggested"
    SENT = "sent"
    ACCEPTED = "accepted"
    DECLINED = "declined"


class InterventionChannel(str, Enum):
    SMS = "sms"
    EMAIL = "email"
    PHARMACIST_CALL = "pharmacist_call"
    PHYSICIAN_ALERT = "physician_alert"


class FillRecord(BaseModel):
    """One dispensing event from a pharmacy portal."""

    fill_date: date
    days_supply: int = Field(gt=0, le=400)
    drug_name: str
    drug_class: DrugClass
    quantity: float | None = None
    pharmacy_id: str | None = None
    ndc: str | None = None


class AdherenceRequest(BaseModel):
    patient_id: str
    fills: list[FillRecord] = Field(default_factory=list)
    period_start: date | None = None
    period_end: date | None = None

    @model_validator(mode="after")
    def _check_period(self) -> AdherenceRequest:
        if self.period_start and self.period_end and self.period_start >= self.period_end:
            raise ValueError("period_start must be before period_end")
        return self


class Gap(BaseModel):
    """A window where the patient held no medication."""

    start: date
    end: date
    days: int
    is_material: bool


class AdherenceScore(BaseModel):
    patient_id: str
    drug_class: DrugClass
    proportion_days_covered: float = Field(ge=0.0, le=1.0)
    medication_possession_ratio: float = Field(ge=0.0)
    band: AdherenceBand
    days_covered: int
    days_in_period: int
    period_start: date
    period_end: date
    fill_count: int
    gaps: list[Gap] = Field(default_factory=list)
    longest_gap_days: int
    days_since_last_fill: int
    persistence_days: int
    risk_score: float | None = None
    risk_score_available: bool
    interpretation: str


class InterventionSuggestion(BaseModel):
    channel: InterventionChannel
    urgency: str
    message: str
    rationale: str


class ReferralCandidate(BaseModel):
    pharmacy_id: str
    pharmacy_name: str
    distance_km: float | None = None
    supports_drug_class: bool
    accepts_new_referrals: bool
    match_score: float | None = None
    match_score_available: bool


class ReferralDecision(BaseModel):
    patient_id: str
    status: ReferralStatus
    reason: str
    candidates: list[ReferralCandidate] = Field(default_factory=list)
    selected_pharmacy_id: str | None = None
    routing_available: bool


class AdherenceResponse(BaseModel):
    score: AdherenceScore
    interventions: list[InterventionSuggestion] = Field(default_factory=list)
    referral: ReferralDecision | None = None
