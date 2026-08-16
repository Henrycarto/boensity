"""Adherence and referral endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.engine import adherence_scorer, referral_router
from app.engine.pharmacy_client import PharmacyClientError, get_pharmacy_client
from app.schemas import (
    AdherenceBand,
    AdherenceRequest,
    AdherenceResponse,
    AdherenceScore,
    Envelope,
    InterventionChannel,
    InterventionSuggestion,
    Meta,
    ReferralDecision,
)

router = APIRouter(tags=["adherence"])


def _meta(**extra) -> Meta:
    return Meta(generated_at=datetime.now(UTC), **extra)


def build_interventions(score: AdherenceScore) -> list[InterventionSuggestion]:
    """What to actually do about this patient.

    Escalation is by how far gone they are, not by how bad the number looks. A
    lapsed patient does not need a text message, and an adherent patient does
    not need anything.
    """
    if score.band is AdherenceBand.ADHERENT:
        return []

    if score.band is AdherenceBand.LAPSED:
        return [
            InterventionSuggestion(
                channel=InterventionChannel.PHYSICIAN_ALERT,
                urgency="high",
                message=(
                    f"Patient has not filled in {score.days_since_last_fill} days. "
                    "Treatment has effectively stopped."
                ),
                rationale=(
                    "Past 90 days without a fill, a reminder assumes an intention to continue "
                    "that the record does not support. This needs a prescriber decision."
                ),
            ),
            InterventionSuggestion(
                channel=InterventionChannel.PHARMACIST_CALL,
                urgency="high",
                message="Call to establish whether the patient stopped deliberately.",
                rationale=(
                    "Side effects and cost are the two most common reasons for silent "
                    "discontinuation, and both have answers that a call can find."
                ),
            ),
        ]

    if score.band is AdherenceBand.NON_ADHERENT:
        return [
            InterventionSuggestion(
                channel=InterventionChannel.PHARMACIST_CALL,
                urgency="medium",
                message=(
                    f"Coverage is {round(score.proportion_days_covered * 100)} percent with a "
                    f"{score.longest_gap_days} day gap. Review regimen and tolerance."
                ),
                rationale=(
                    "Below 50 percent coverage the problem is usually tolerance or belief, "
                    "and neither responds to an automated reminder."
                ),
            ),
        ]

    suggestions = [
        InterventionSuggestion(
            channel=InterventionChannel.SMS,
            urgency="low",
            message="Refill reminder before the next dose is due.",
            rationale=(
                f"Coverage of {round(score.proportion_days_covered * 100)} percent with no long "
                "gap is a timing problem, which is what reminders are good at."
            ),
        )
    ]

    if score.longest_gap_days >= 60:
        suggestions.append(
            InterventionSuggestion(
                channel=InterventionChannel.PHARMACIST_CALL,
                urgency="medium",
                message=f"A {score.longest_gap_days} day gap is worth a call, not just a text.",
                rationale=(
                    "Gaps this long are usually logistics: supply, transport, or a prescription "
                    "that lapsed without anyone noticing."
                ),
            )
        )

    return suggestions


@router.post("/adherence", response_model=Envelope[AdherenceResponse])
async def score(request: AdherenceRequest) -> Envelope[AdherenceResponse]:
    """Score a fill history supplied by the caller."""
    result = adherence_scorer.score_adherence(
        patient_id=request.patient_id,
        fills=request.fills,
        period_start=request.period_start,
        period_end=request.period_end,
    )

    return Envelope(
        data=AdherenceResponse(
            score=result,
            interventions=build_interventions(result),
            referral=None,
        ),
        error=None,
        meta=_meta(
            private_scoring_available=adherence_scorer.private_scoring_available(),
            pdc_threshold=adherence_scorer.PDC_ADHERENT,
        ),
    )


@router.get("/adherence/{patient_id}", response_model=Envelope[AdherenceResponse])
async def score_from_pharmacy(patient_id: str) -> Envelope[AdherenceResponse]:
    """Pull fills from the connected pharmacy network and score them.

    Reports whether the data was live in meta. A demo running on fixtures must
    never be mistakable for a live pharmacy feed.
    """
    client = get_pharmacy_client()

    try:
        fills, is_live = await client.fetch_fills(patient_id)
    except PharmacyClientError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    result = adherence_scorer.score_adherence(patient_id=patient_id, fills=fills)

    try:
        candidates, _ = await client.fetch_referral_candidates(patient_id, result.drug_class)
    except PharmacyClientError:
        candidates = []

    decision = referral_router.route(result, candidates)

    return Envelope(
        data=AdherenceResponse(
            score=result,
            interventions=build_interventions(result),
            referral=decision,
        ),
        error=None,
        meta=_meta(
            live_pharmacy_data=is_live,
            private_scoring_available=adherence_scorer.private_scoring_available(),
            private_routing_available=referral_router.private_routing_available(),
        ),
    )


class ReferralRequest(BaseModel):
    patient_id: str
    pharmacy_id: str
    note: str = ""


@router.post("/referrals", response_model=Envelope[dict])
async def send_referral(request: ReferralRequest) -> Envelope[dict]:
    client = get_pharmacy_client()

    try:
        result, is_live = await client.send_referral(request.patient_id, request.pharmacy_id, request.note)
    except PharmacyClientError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    return Envelope(data=result, error=None, meta=_meta(live_pharmacy_data=is_live))


@router.post("/referrals/evaluate", response_model=Envelope[ReferralDecision])
async def evaluate_referral(request: AdherenceRequest) -> Envelope[ReferralDecision]:
    """Whether a referral is indicated, without sending one."""
    result = adherence_scorer.score_adherence(
        patient_id=request.patient_id,
        fills=request.fills,
        period_start=request.period_start,
        period_end=request.period_end,
    )

    client = get_pharmacy_client()
    try:
        candidates, is_live = await client.fetch_referral_candidates(request.patient_id, result.drug_class)
    except PharmacyClientError:
        candidates, is_live = [], False

    decision = referral_router.route(result, candidates)

    return Envelope(
        data=decision,
        error=None,
        meta=_meta(
            live_pharmacy_data=is_live,
            private_routing_available=referral_router.private_routing_available(),
        ),
    )
