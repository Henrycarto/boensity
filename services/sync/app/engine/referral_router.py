"""Physician to pharmacy referral routing.

WHAT IS IN THIS FILE
--------------------
The clinical half: deciding whether a patient needs a specialty pharmacy at
all, and filtering the candidates that can actually serve them. A denosumab
patient needs a pharmacy that stocks a cold chain injectable and can schedule
an administration. An oral bisphosphonate patient who is simply forgetting
doses needs a reminder, not a referral. That logic is auditable and public,
because a physician has to be able to see why their patient was routed.

WHAT IS NOT IN THIS FILE
------------------------
The commission model and the ranking that orders qualified pharmacies against
each other live in boensity_sync_private. That is the revenue logic of the
pharmacy layer and it is deliberately not in the public repository. See
rank_candidates below.

The split is not only commercial. A physician needs to know that clinical
eligibility is decided separately from commercial ranking, and keeping the two
in different packages is the clearest way to demonstrate it. When ranking is
unavailable the service returns candidates in clinical eligibility order and
reports routing_available false rather than presenting an arbitrary order as a
recommendation.
"""

from __future__ import annotations

import logging

from app.schemas import (
    AdherenceBand,
    AdherenceScore,
    DrugClass,
    ReferralCandidate,
    ReferralDecision,
    ReferralStatus,
)

logger = logging.getLogger("boensity.sync.referral")

# Drug classes that need a specialty pharmacy: cold chain handling, scheduled
# administration, or prior authorisation support a retail counter cannot give.
SPECIALTY_REQUIRED: frozenset[DrugClass] = frozenset(
    {DrugClass.DENOSUMAB, DrugClass.IV_BISPHOSPHONATE, DrugClass.ANABOLIC}
)

# Below this PDC an oral patient has a behavioural problem a different pharmacy
# will not solve. Referring them moves the patient without helping them.
ORAL_REFERRAL_PDC_FLOOR = 0.40


def referral_is_clinically_indicated(score: AdherenceScore) -> tuple[bool, str]:
    """Whether this patient needs a different pharmacy, on clinical grounds only."""
    if score.drug_class in SPECIALTY_REQUIRED:
        if score.band in {AdherenceBand.LAPSED, AdherenceBand.NON_ADHERENT}:
            return True, (
                f"{score.drug_class.value.replace('_', ' ')} requires scheduled administration "
                f"and this patient is {score.band.value.replace('_', ' ')}. A specialty pharmacy "
                "can take over scheduling and cold chain handling."
            )
        return False, (
            "On a specialty agent and currently on schedule. No referral needed " "while adherence holds."
        )

    if score.band is AdherenceBand.ADHERENT:
        return False, "Adherent on an oral agent. No referral indicated."

    if score.proportion_days_covered < ORAL_REFERRAL_PDC_FLOOR:
        # Very low coverage on an oral agent is usually tolerance or belief,
        # not logistics. Changing pharmacy does not address either.
        return False, (
            f"Coverage is {round(score.proportion_days_covered * 100)} percent, low enough that "
            "the barrier is unlikely to be dispensing. This needs a prescriber review of "
            "tolerance and regimen before any pharmacy change."
        )

    if score.longest_gap_days >= 60:
        return True, (
            f"A {score.longest_gap_days} day gap on an oral agent with otherwise partial "
            "coverage points at refill logistics, which a pharmacy with synchronised refills "
            "and delivery can fix."
        )

    return False, "Partial adherence without a material supply gap. Reminders before referral."


def filter_eligible(candidates: list[ReferralCandidate], drug_class: DrugClass) -> list[ReferralCandidate]:
    """Candidates that can actually dispense this drug and are taking referrals."""
    return [
        candidate
        for candidate in candidates
        if candidate.supports_drug_class and candidate.accepts_new_referrals
    ]


# ---------------------------------------------------------------------------
# Private ranking
# ---------------------------------------------------------------------------

try:  # pragma: no cover - the private package is not part of this repository
    from boensity_sync_private.routing import rank_pharmacies as _rank_pharmacies

    _PRIVATE_ROUTING_AVAILABLE = True
except ImportError:
    _rank_pharmacies = None
    _PRIVATE_ROUTING_AVAILABLE = False


def private_routing_available() -> bool:
    return _PRIVATE_ROUTING_AVAILABLE


def rank_candidates(
    candidates: list[ReferralCandidate],
    score: AdherenceScore,
) -> tuple[list[ReferralCandidate], bool]:
    """Order qualified pharmacies and attach a match score.

    PRIVATE. The ranking weights, the commission model, and the network
    agreement terms live in boensity_sync_private and are not committed here.

    Returns the candidates plus a flag saying whether ranking actually ran. On
    the public path the list comes back in clinical eligibility order with
    match_score None, and the caller reports routing_available false. Silently
    returning an unranked list as though it were ranked would misrepresent a
    commercial recommendation as a clinical one.
    """
    if not _PRIVATE_ROUTING_AVAILABLE or _rank_pharmacies is None:
        return candidates, False

    ranked = _rank_pharmacies(
        candidates=[candidate.model_dump() for candidate in candidates],
        drug_class=score.drug_class.value,
        adherence_band=score.band.value,
    )
    return [ReferralCandidate.model_validate(entry) for entry in ranked], True


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def route(
    score: AdherenceScore,
    candidates: list[ReferralCandidate] | None = None,
) -> ReferralDecision:
    candidates = candidates or []
    indicated, reason = referral_is_clinically_indicated(score)

    if not indicated:
        return ReferralDecision(
            patient_id=score.patient_id,
            status=ReferralStatus.NOT_REQUIRED,
            reason=reason,
            candidates=[],
            selected_pharmacy_id=None,
            routing_available=private_routing_available(),
        )

    eligible = filter_eligible(candidates, score.drug_class)

    if not eligible:
        return ReferralDecision(
            patient_id=score.patient_id,
            status=ReferralStatus.SUGGESTED,
            reason=(
                f"{reason} No pharmacy in the connected network currently supports "
                f"{score.drug_class.value.replace('_', ' ')} referrals for this patient."
            ),
            candidates=[],
            selected_pharmacy_id=None,
            routing_available=private_routing_available(),
        )

    ranked, routing_available = rank_candidates(eligible, score)

    # A pharmacy is only preselected when the ranking that chose it is
    # available to explain the choice.
    selected = ranked[0].pharmacy_id if (routing_available and ranked) else None

    return ReferralDecision(
        patient_id=score.patient_id,
        status=ReferralStatus.SUGGESTED,
        reason=reason,
        candidates=ranked,
        selected_pharmacy_id=selected,
        routing_available=routing_available,
    )
