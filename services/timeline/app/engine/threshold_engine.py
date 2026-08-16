"""Danger zone crossing detection and alerting.

This is the module that turns a line on a chart into a clinical event. The
question a physician actually has is not "what is this patient's T-score" but
"when does this patient become someone I should have treated already".

Crossing detection is intentionally conservative. A false negative delays
treatment by one scan interval. A false positive erodes trust in the alert,
and an alert nobody trusts is worse than no alert, so every crossing carries a
confidence grade derived from how much data the fit actually rests on.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from app.engine.frax_calculator import (
    INTERVENTION_THRESHOLD_HIP,
    INTERVENTION_THRESHOLD_MAJOR,
    FraxResult,
)
from app.engine.trajectory_model import (
    DAYS_PER_YEAR,
    OSTEOPENIA_THRESHOLD,
    OSTEOPOROSIS_THRESHOLD,
    TrajectoryFit,
    classify_band,
    least_significant_change,
)
from app.schemas import (
    Alert,
    AlertSeverity,
    DiagnosticBand,
    Manufacturer,
    ScanObservation,
    ThresholdCrossing,
    TrendDirection,
    VelocityReport,
)

# A scan older than this is no longer a current picture of the patient. Most
# guidelines put repeat DEXA at one to two years on treatment and two to five
# years otherwise, so three years without a scan is a real gap.
STALE_SCAN_YEARS = 3.0

# Losing more than this per year is fast enough to warrant looking for a
# secondary cause rather than assuming age-related loss.
RAPID_LOSS_PER_YEAR = 0.10

Confidence = Literal["high", "moderate", "low"]


def _crossing_confidence(fit: TrajectoryFit, velocity: VelocityReport) -> Confidence:
    if fit.is_inferred_from_population:
        return "low"
    if not velocity.exceeds_least_significant_change:
        return "low"

    span = float(fit.x_values[-1] - fit.x_values[0])

    # A confidence interval on the slope that still contains zero means the
    # direction itself is not established, whatever the point estimate says.
    if (
        velocity.ci_lower is not None
        and velocity.ci_upper is not None
        and velocity.ci_lower <= 0.0 <= velocity.ci_upper
    ):
        return "low"

    if fit.n >= 4 and span >= 5.0:
        return "high"
    if fit.n >= 3 and span >= 3.0:
        return "moderate"
    return "low"


def detect_crossing(
    fit: TrajectoryFit,
    velocity: VelocityReport,
    threshold: float,
    band_entered: DiagnosticBand,
    projection_years: int,
) -> ThresholdCrossing:
    """Where the fitted line meets one threshold.

    Solved analytically rather than by scanning the sampled points, so the
    answer does not depend on the chart's sampling resolution.
    """
    last_observed_years = float(fit.x_values[-1])
    current_value = fit.value_at(last_observed_years)
    confidence = _crossing_confidence(fit, velocity)

    if current_value <= threshold:
        return ThresholdCrossing(
            threshold_t_score=threshold,
            band_entered=band_entered,
            crossing_date=None,
            years_until_crossing=None,
            already_crossed=True,
            confidence=confidence,
        )

    # A flat or improving line never reaches a threshold below it. Reporting a
    # crossing 200 years out would be arithmetically true and clinically absurd.
    if fit.slope_per_year >= 0:
        return ThresholdCrossing(
            threshold_t_score=threshold,
            band_entered=band_entered,
            crossing_date=None,
            years_until_crossing=None,
            already_crossed=False,
            confidence=confidence,
        )

    years_from_baseline = (threshold - fit.intercept) / fit.slope_per_year
    years_from_now = years_from_baseline - last_observed_years

    if years_from_now > projection_years:
        return ThresholdCrossing(
            threshold_t_score=threshold,
            band_entered=band_entered,
            crossing_date=None,
            years_until_crossing=round(years_from_now, 2),
            already_crossed=False,
            confidence=confidence,
        )

    crossing_date = fit.baseline_date + timedelta(days=round(years_from_baseline * DAYS_PER_YEAR))

    return ThresholdCrossing(
        threshold_t_score=threshold,
        band_entered=band_entered,
        crossing_date=crossing_date,
        years_until_crossing=round(years_from_now, 2),
        already_crossed=False,
        confidence=confidence,
    )


def detect_all_crossings(
    fit: TrajectoryFit,
    velocity: VelocityReport,
    projection_years: int,
) -> list[ThresholdCrossing]:
    return [
        detect_crossing(fit, velocity, OSTEOPENIA_THRESHOLD, DiagnosticBand.OSTEOPENIA, projection_years),
        detect_crossing(fit, velocity, OSTEOPOROSIS_THRESHOLD, DiagnosticBand.OSTEOPOROSIS, projection_years),
    ]


def _osteoporosis_crossing(crossings: list[ThresholdCrossing]) -> ThresholdCrossing | None:
    for crossing in crossings:
        if crossing.band_entered is DiagnosticBand.OSTEOPOROSIS:
            return crossing
    return None


def build_alerts(
    fit: TrajectoryFit,
    velocity: VelocityReport,
    crossings: list[ThresholdCrossing],
    scans: list[ScanObservation],
    current_t_score: float,
    frax: FraxResult | None = None,
    today: date | None = None,
) -> list[Alert]:
    """Everything the system wants to say about this patient, ordered by severity."""
    today = today or date.today()
    alerts: list[Alert] = []
    current_band = classify_band(current_t_score)
    osteoporosis = _osteoporosis_crossing(crossings)

    # ---- Where the patient is now ----
    if current_band is DiagnosticBand.OSTEOPOROSIS:
        alerts.append(
            Alert(
                code="already_osteoporotic",
                severity=AlertSeverity.CRITICAL,
                title="Already in the osteoporosis range",
                detail=(
                    f"Most recent T-score is {current_t_score:.1f}, at or below the WHO "
                    f"osteoporosis cutoff of {OSTEOPOROSIS_THRESHOLD}. This is a treatment "
                    "decision today, not a monitoring decision."
                ),
            )
        )

    # ---- Where the patient is going ----
    elif osteoporosis is not None and osteoporosis.crossing_date is not None:
        years = osteoporosis.years_until_crossing or 0.0
        severity = AlertSeverity.CRITICAL if years <= 5.0 else AlertSeverity.WARNING
        alerts.append(
            Alert(
                code="projected_osteoporosis_crossing",
                severity=severity,
                title=f"Projected to cross the osteoporosis threshold in {years:.1f} years",
                detail=(
                    f"At the current rate of {abs(velocity.t_score_per_year):.3f} T-score units "
                    f"per year, this patient reaches {OSTEOPOROSIS_THRESHOLD} around "
                    f"{osteoporosis.crossing_date.strftime('%B %Y')}. "
                    f"Projection confidence is {osteoporosis.confidence}."
                ),
            )
        )

    if current_band is DiagnosticBand.OSTEOPENIA and (
        osteoporosis is None or osteoporosis.crossing_date is None
    ):
        alerts.append(
            Alert(
                code="osteopenia_stable",
                severity=AlertSeverity.INFO,
                title="In the osteopenia range, not projected to cross",
                detail=(
                    f"T-score {current_t_score:.1f} sits between {OSTEOPENIA_THRESHOLD} and "
                    f"{OSTEOPOROSIS_THRESHOLD}. The current trend does not reach the "
                    "osteoporosis threshold inside the projection window."
                ),
            )
        )

    # ---- How fast ----
    if (
        velocity.direction is TrendDirection.DECLINING
        and abs(velocity.t_score_per_year) >= RAPID_LOSS_PER_YEAR
        and not fit.is_inferred_from_population
    ):
        alerts.append(
            Alert(
                code="rapid_bone_loss",
                severity=AlertSeverity.WARNING,
                title="Rate of loss above the expected age-related range",
                detail=(
                    f"Losing {abs(velocity.t_score_per_year):.3f} T-score units per year. "
                    "Loss this fast is worth a workup for a secondary cause: vitamin D "
                    "deficiency, hyperparathyroidism, malabsorption, or a medication effect."
                ),
            )
        )

    if velocity.direction is TrendDirection.IMPROVING:
        alerts.append(
            Alert(
                code="treatment_response",
                severity=AlertSeverity.INFO,
                title="Bone density is gaining",
                detail=(
                    f"Gaining {velocity.t_score_per_year:.3f} T-score units per year, above the "
                    "least significant change for this site. Consistent with an effective "
                    "treatment response."
                ),
            )
        )

    # ---- Absolute risk ----
    if frax is not None:
        if frax.major_osteoporotic_percent >= INTERVENTION_THRESHOLD_MAJOR:
            alerts.append(
                Alert(
                    code="frax_major_threshold",
                    severity=AlertSeverity.CRITICAL,
                    title=f"FRAX major osteoporotic risk {frax.major_osteoporotic_percent}%",
                    detail=(
                        f"At or above the {INTERVENTION_THRESHOLD_MAJOR}% intervention threshold "
                        "for major osteoporotic fracture."
                    ),
                )
            )
        if frax.hip_percent >= INTERVENTION_THRESHOLD_HIP:
            alerts.append(
                Alert(
                    code="frax_hip_threshold",
                    severity=AlertSeverity.CRITICAL,
                    title=f"FRAX hip fracture risk {frax.hip_percent}%",
                    detail=(
                        f"At or above the {INTERVENTION_THRESHOLD_HIP}% intervention threshold "
                        "for hip fracture."
                    ),
                )
            )

    # ---- Data quality ----
    latest_scan_date = max(s.scan_date for s in scans if s.site == fit.site)
    years_since_scan = (today - latest_scan_date).days / DAYS_PER_YEAR
    if years_since_scan >= STALE_SCAN_YEARS:
        alerts.append(
            Alert(
                code="stale_scan",
                severity=AlertSeverity.WARNING,
                title=f"Last scan was {years_since_scan:.1f} years ago",
                detail=(
                    f"Most recent DEXA at this site is dated {latest_scan_date.isoformat()}. "
                    "The projection past that date is extrapolation, and the further it runs "
                    "the less it should be trusted."
                ),
            )
        )

    if fit.is_inferred_from_population:
        alerts.append(
            Alert(
                code="single_scan_only",
                severity=AlertSeverity.WARNING,
                title="One scan on file, no measured trend",
                detail=(
                    "The projected line uses the population average rate of loss for this age "
                    "and sex. It is not this patient's measured decline. A repeat scan converts "
                    "this from an estimate into a trajectory."
                ),
            )
        )

    vendors = {s.manufacturer for s in scans if s.site == fit.site} - {Manufacturer.UNKNOWN}
    if len(vendors) > 1:
        alerts.append(
            Alert(
                code="mixed_manufacturer_series",
                severity=AlertSeverity.WARNING,
                title="Scans come from more than one manufacturer",
                detail=(
                    f"This series mixes {' and '.join(sorted(v.value for v in vendors))} scanners. "
                    "Vendor T-scores are not directly comparable. Values were standardised on "
                    "ingest, but a residual step change of roughly 0.2 T-score units can remain."
                ),
            )
        )

    if not velocity.exceeds_least_significant_change and not fit.is_inferred_from_population:
        lsc = least_significant_change(fit.site)
        alerts.append(
            Alert(
                code="change_within_noise",
                severity=AlertSeverity.INFO,
                title="Change is inside the measurement noise floor",
                detail=(
                    f"Total change across the series is smaller than the least significant "
                    f"change for this site ({lsc:.2f} T-score units). Any apparent trend here "
                    "cannot be separated from scanner precision error."
                ),
            )
        )

    severity_rank = {AlertSeverity.CRITICAL: 0, AlertSeverity.WARNING: 1, AlertSeverity.INFO: 2}
    alerts.sort(key=lambda alert: severity_rank[alert.severity])
    return alerts
