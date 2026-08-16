"""Fill rate and gap detection.

WHAT IS IN THIS FILE
--------------------
The published, standard measures. Proportion of Days Covered is what CMS,
pharmacy benefit managers, and the adherence literature all use, and its 0.80
threshold is the number every payer already recognises. Implementing it openly
matters: a pharmacy director will not act on a score they cannot audit, and
"our model says this patient is at risk" is not an argument. "This patient
covered 61 percent of days and has a 74 day gap" is.

WHAT IS NOT IN THIS FILE
------------------------
The composite Boensity risk score that weights PDC against gap timing, drug
class, refill trajectory, and prescriber pattern lives in the private package
boensity_sync_private. That weighting is derived from proprietary pharmacy
network data and is the part a competitor could not reconstruct from the
public repository. See the note at compute_risk_score below.

Everything degrades cleanly without it. PDC, MPR, gaps, persistence, and the
adherence band are all computed here, and the API reports risk_score_available
false rather than inventing a number.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from app.schemas import (
    AdherenceBand,
    AdherenceScore,
    DrugClass,
    FillRecord,
    Gap,
)

logger = logging.getLogger("boensity.sync.adherence")

# The standard threshold. Below 0.80 PDC, bisphosphonate anti-fracture benefit
# drops sharply, which is what makes this a clinical cutoff and not just an
# administrative one.
PDC_ADHERENT = 0.80
PDC_PARTIAL = 0.50

# A gap shorter than this is a late refill, not a discontinuation. Flagging
# every three day slip would bury the patients who genuinely stopped.
MATERIAL_GAP_DAYS = 30

# Past this with no fill the patient has effectively discontinued.
LAPSED_DAYS = 90

# Expected days of cover per administration, used when days_supply is absent
# or wrong. Injectables are the reason this table exists: a denosumab fill has
# a days_supply of 1 in most pharmacy feeds, and taking that literally would
# score every denosumab patient as catastrophically non-adherent when they are
# perfectly on schedule.
EXPECTED_COVERAGE_DAYS: dict[DrugClass, int] = {
    DrugClass.ORAL_BISPHOSPHONATE: 30,
    DrugClass.IV_BISPHOSPHONATE: 365,
    DrugClass.DENOSUMAB: 180,
    DrugClass.SERM: 30,
    DrugClass.ANABOLIC: 28,
    DrugClass.SUPPLEMENT: 30,
}

# Grace period before an injectable counts as late. Clinics schedule around
# holidays and appointment availability.
INJECTABLE_GRACE_DAYS = 28


def _effective_days_supply(fill: FillRecord) -> int:
    """Days of cover a fill actually provides.

    A days_supply of 1 on a six month injection is a data entry artefact of
    how pharmacy systems record an administration, not a real one day supply.
    """
    expected = EXPECTED_COVERAGE_DAYS.get(fill.drug_class, 30)

    if fill.drug_class in {DrugClass.DENOSUMAB, DrugClass.IV_BISPHOSPHONATE}:
        return expected if fill.days_supply <= 7 else fill.days_supply

    return fill.days_supply


def _coverage_days(fills: list[FillRecord], start: date, end: date) -> set[date]:
    """Distinct days covered inside the window.

    A set, not a sum, because overlapping fills must not double count. A
    patient who refills early every month would otherwise score above 100
    percent coverage while actually stockpiling.
    """
    covered: set[date] = set()
    for fill in fills:
        supply = _effective_days_supply(fill)
        for offset in range(supply):
            day = fill.fill_date + timedelta(days=offset)
            if start <= day <= end:
                covered.add(day)
    return covered


def find_gaps(fills: list[FillRecord], start: date, end: date) -> list[Gap]:
    """Uncovered stretches, in order."""
    if not fills:
        return [Gap(start=start, end=end, days=(end - start).days + 1, is_material=True)]

    covered = _coverage_days(fills, start, end)
    gaps: list[Gap] = []

    current_start: date | None = None
    day = start
    while day <= end:
        if day not in covered:
            if current_start is None:
                current_start = day
        elif current_start is not None:
            gap_days = (day - current_start).days
            gaps.append(
                Gap(
                    start=current_start,
                    end=day - timedelta(days=1),
                    days=gap_days,
                    is_material=gap_days >= MATERIAL_GAP_DAYS,
                )
            )
            current_start = None
        day += timedelta(days=1)

    if current_start is not None:
        gap_days = (end - current_start).days + 1
        gaps.append(
            Gap(
                start=current_start,
                end=end,
                days=gap_days,
                is_material=gap_days >= MATERIAL_GAP_DAYS,
            )
        )

    return gaps


def proportion_days_covered(fills: list[FillRecord], start: date, end: date) -> tuple[float, int, int]:
    total_days = (end - start).days + 1
    if total_days <= 0:
        return 0.0, 0, 0
    covered = len(_coverage_days(fills, start, end))
    return round(covered / total_days, 4), covered, total_days


def medication_possession_ratio(fills: list[FillRecord], start: date, end: date) -> float:
    """Total supply dispensed over the observation window.

    Reported alongside PDC because they diverge in a clinically informative
    way: MPR above 1.0 with PDC well below it means the patient is refilling
    but not taking, which is a different conversation from not refilling.
    """
    total_days = (end - start).days + 1
    if total_days <= 0:
        return 0.0
    supply = sum(_effective_days_supply(fill) for fill in fills if start <= fill.fill_date <= end)
    return round(supply / total_days, 4)


def _band(pdc: float, days_since_last_fill: int) -> AdherenceBand:
    if days_since_last_fill >= LAPSED_DAYS:
        return AdherenceBand.LAPSED
    if pdc >= PDC_ADHERENT:
        return AdherenceBand.ADHERENT
    if pdc >= PDC_PARTIAL:
        return AdherenceBand.PARTIAL
    return AdherenceBand.NON_ADHERENT


# ---------------------------------------------------------------------------
# Private scoring
# ---------------------------------------------------------------------------

try:  # pragma: no cover - the private package is not part of this repository
    from boensity_sync_private.scoring import composite_risk_score as _composite_risk_score

    _PRIVATE_SCORING_AVAILABLE = True
except ImportError:
    _composite_risk_score = None
    _PRIVATE_SCORING_AVAILABLE = False


def private_scoring_available() -> bool:
    return _PRIVATE_SCORING_AVAILABLE


def compute_risk_score(
    pdc: float,
    gaps: list[Gap],
    drug_class: DrugClass,
    days_since_last_fill: int,
    persistence_days: int,
) -> float | None:
    """Composite non-adherence risk, 0 to 1.

    PRIVATE. The weighting coefficients live in boensity_sync_private, which is
    not committed to this repository. They were fitted against pharmacy network
    outcome data and are the commercially sensitive part of Sync.

    Returns None when the private package is absent. Callers must check
    risk_score_available rather than treating None as zero risk, and the API
    surfaces the distinction rather than hiding it behind a default.
    """
    if not _PRIVATE_SCORING_AVAILABLE or _composite_risk_score is None:
        return None

    return _composite_risk_score(
        pdc=pdc,
        gap_days=[gap.days for gap in gaps],
        drug_class=drug_class.value,
        days_since_last_fill=days_since_last_fill,
        persistence_days=persistence_days,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def score_adherence(
    patient_id: str,
    fills: list[FillRecord],
    period_start: date | None = None,
    period_end: date | None = None,
    today: date | None = None,
) -> AdherenceScore:
    today = today or date.today()
    period_end = period_end or today

    if period_start is None:
        # Default to a year, the window payers and the adherence literature
        # both use, so the number is comparable to published benchmarks.
        period_start = period_end - timedelta(days=364)

    ordered = sorted(fills, key=lambda fill: fill.fill_date)
    in_window = [fill for fill in ordered if period_start <= fill.fill_date <= period_end]

    drug_class = ordered[-1].drug_class if ordered else DrugClass.ORAL_BISPHOSPHONATE

    pdc, days_covered, days_in_period = proportion_days_covered(in_window, period_start, period_end)
    mpr = medication_possession_ratio(in_window, period_start, period_end)
    gaps = find_gaps(in_window, period_start, period_end)
    longest_gap = max((gap.days for gap in gaps), default=0)

    if ordered:
        last_fill = ordered[-1]
        days_since_last_fill = (today - last_fill.fill_date).days
        persistence_days = (last_fill.fill_date - ordered[0].fill_date).days + _effective_days_supply(
            last_fill
        )
    else:
        days_since_last_fill = (today - period_start).days
        persistence_days = 0

    band = _band(pdc, days_since_last_fill)
    risk_score = compute_risk_score(pdc, gaps, drug_class, days_since_last_fill, persistence_days)

    return AdherenceScore(
        patient_id=patient_id,
        drug_class=drug_class,
        proportion_days_covered=pdc,
        medication_possession_ratio=mpr,
        band=band,
        days_covered=days_covered,
        days_in_period=days_in_period,
        period_start=period_start,
        period_end=period_end,
        fill_count=len(in_window),
        gaps=[gap for gap in gaps if gap.is_material],
        longest_gap_days=longest_gap,
        days_since_last_fill=days_since_last_fill,
        persistence_days=persistence_days,
        risk_score=risk_score,
        risk_score_available=risk_score is not None,
        interpretation=_interpret(band, pdc, longest_gap, days_since_last_fill, len(in_window)),
    )


def _interpret(
    band: AdherenceBand,
    pdc: float,
    longest_gap: int,
    days_since_last_fill: int,
    fill_count: int,
) -> str:
    percent = round(pdc * 100)

    if fill_count == 0:
        return (
            "No fills recorded in this period. Either the prescription was never collected "
            "or the pharmacy feed is not connected for this patient."
        )

    if band is AdherenceBand.LAPSED:
        return (
            f"No fill in {days_since_last_fill} days. Treat as discontinued until confirmed "
            "otherwise. Restarting is a prescriber conversation, not a refill reminder."
        )

    if band is AdherenceBand.ADHERENT:
        return f"Covered {percent} percent of days, at or above the 80 percent adherence threshold."

    if band is AdherenceBand.PARTIAL:
        return (
            f"Covered {percent} percent of days, below the 80 percent threshold where "
            f"anti-fracture benefit holds. Longest gap was {longest_gap} days."
        )

    return (
        f"Covered {percent} percent of days with a longest gap of {longest_gap} days. "
        "At this level the treatment is unlikely to be changing fracture risk."
    )
