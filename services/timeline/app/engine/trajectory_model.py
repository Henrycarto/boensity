"""T-score velocity and decline modelling.

The clinical problem this module exists to solve: a DEXA report tells a
physician where a patient is today. It never tells them how fast they are
moving or when they will arrive at the fracture threshold. Two patients can
both sit at T -1.8 and have completely different futures. One has been flat
for six years. The other lost 0.9 T-score units in four. Static reporting
treats them identically.

Two measurement realities shape every decision below.

First, densitometry has real precision error. A change smaller than the least
significant change (LSC) is scanner noise, not bone loss. The ISCD defines LSC
as 2.77 times the precision error at 95% confidence. Reporting a trend that
sits inside the noise floor would manufacture urgency, which is the exact
failure mode a clinical warning system cannot have.

Second, T-scores from different manufacturers are not directly comparable, so
this module fits on whatever the caller supplies but flags mixed-vendor series.
The Ingest service is responsible for standardising BMD before it gets here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from scipy import stats

from app.schemas import (
    DiagnosticBand,
    Manufacturer,
    ScanObservation,
    Sex,
    SkeletalSite,
    TrajectoryPoint,
    TrendDirection,
    VelocityReport,
)

DAYS_PER_YEAR = 365.2425

# WHO 1994 densitometric cutoffs.
OSTEOPENIA_THRESHOLD = -1.0
OSTEOPOROSIS_THRESHOLD = -2.5


@dataclass(frozen=True)
class SiteReference:
    """Per-site constants needed to turn BMD noise into T-score noise.

    Young adult reference values are NHANES III femoral neck derived figures
    for the hip sites and manufacturer reference data for spine and forearm.
    Precision errors are typical in-vivo RMS-SD values for a trained
    technologist; a deployment should replace them with its own site-measured
    precision, which is why they live in one editable table.
    """

    precision_error_percent: float
    young_adult_mean_bmd: float
    young_adult_sd_bmd: float


SITE_REFERENCE: dict[SkeletalSite, SiteReference] = {
    SkeletalSite.FEMORAL_NECK: SiteReference(1.6, 0.858, 0.120),
    SkeletalSite.TOTAL_HIP: SiteReference(1.2, 0.942, 0.122),
    SkeletalSite.LUMBAR_SPINE: SiteReference(1.4, 1.047, 0.110),
    SkeletalSite.FOREARM: SiteReference(1.8, 0.585, 0.060),
}

# Population average annual T-score change, used only when a single scan gives
# us no slope of our own. Postmenopausal women lose femoral neck BMD at roughly
# one percent a year; men at roughly half that.
POPULATION_ANNUAL_T_CHANGE: dict[Sex, float] = {
    Sex.FEMALE: -0.075,
    Sex.MALE: -0.040,
}


def classify_band(t_score: float) -> DiagnosticBand:
    """WHO band for a T-score. Boundaries are inclusive downward, matching the
    convention that exactly -2.5 is osteoporosis."""
    if t_score <= OSTEOPOROSIS_THRESHOLD:
        return DiagnosticBand.OSTEOPOROSIS
    if t_score <= OSTEOPENIA_THRESHOLD:
        return DiagnosticBand.OSTEOPENIA
    return DiagnosticBand.NORMAL


def least_significant_change(site: SkeletalSite) -> float:
    """LSC expressed in T-score units.

    Converting through BMD is the only defensible route: precision error is a
    property of the densitometer in g/cm2, and the T-score denominator is the
    young adult SD.
    """
    ref = SITE_REFERENCE[site]
    lsc_bmd = 2.77 * (ref.precision_error_percent / 100.0) * ref.young_adult_mean_bmd
    return lsc_bmd / ref.young_adult_sd_bmd


@dataclass
class TrajectoryFit:
    """Result of fitting the decline line, before it is turned into API shapes."""

    intercept: float
    slope_per_year: float
    slope_stderr: float | None
    r_squared: float | None
    residual_sd: float | None
    degrees_of_freedom: int
    x_values: np.ndarray
    x_mean: float
    x_sum_squares: float
    baseline_date: date
    site: SkeletalSite
    n: int
    is_inferred_from_population: bool

    def value_at(self, years: float) -> float:
        return self.intercept + self.slope_per_year * years

    def prediction_interval(self, years: float, confidence: float = 0.95) -> tuple[float, float] | None:
        """Interval for a future observation, not for the mean line.

        A physician reads the shaded band as the range the patient could
        actually land in, so the wider prediction interval is the honest
        choice over a confidence interval on the fitted mean.
        """
        if self.residual_sd is None or self.degrees_of_freedom < 1:
            return None

        centred = years - self.x_mean
        # Variance inflation for a single future observation. Grows with
        # distance from the centre of the observed window, which is why the
        # band on the chart widens as the projection runs further out.
        variance_factor = 1.0 + (1.0 / self.n) + (centred * centred / self.x_sum_squares)
        margin_sd = self.residual_sd * math.sqrt(variance_factor)
        t_crit = float(stats.t.ppf(1.0 - (1.0 - confidence) / 2.0, self.degrees_of_freedom))
        margin = t_crit * margin_sd
        centre = self.value_at(years)
        return centre - margin, centre + margin


def _years_between(earlier: date, later: date) -> float:
    return (later - earlier).days / DAYS_PER_YEAR


def _date_at_offset(baseline: date, years: float) -> date:
    return baseline + timedelta(days=round(years * DAYS_PER_YEAR))


def fit_trajectory(
    scans: list[ScanObservation],
    site: SkeletalSite,
    sex: Sex,
) -> TrajectoryFit:
    """Fit T-score against time in years for one skeletal site.

    Ordinary least squares is deliberate. Serial DEXA gives few points over
    many years, and the underlying biology over a decade is close enough to
    linear that a higher-order fit would chase measurement noise and produce
    projections that swing wildly on one new scan.
    """
    series = sorted((s for s in scans if s.site == site), key=lambda s: s.scan_date)
    if not series:
        raise ValueError(f"no scans at site {site.value}")

    baseline_date = series[0].scan_date
    x = np.array([_years_between(baseline_date, s.scan_date) for s in series], dtype=float)
    y = np.array([s.t_score for s in series], dtype=float)
    n = len(series)

    if n == 1:
        # One scan is a point, not a trajectory. We still return a usable line
        # so the chart can render something, but it is explicitly labelled as
        # a population prior rather than this patient's measured decline.
        return TrajectoryFit(
            intercept=float(y[0]),
            slope_per_year=POPULATION_ANNUAL_T_CHANGE[sex],
            slope_stderr=None,
            r_squared=None,
            residual_sd=None,
            degrees_of_freedom=0,
            x_values=x,
            x_mean=0.0,
            x_sum_squares=0.0,
            baseline_date=baseline_date,
            site=site,
            n=1,
            is_inferred_from_population=True,
        )

    x_mean = float(np.mean(x))
    x_sum_squares = float(np.sum((x - x_mean) ** 2))

    if x_sum_squares == 0.0:
        raise ValueError("all scans share the same date; cannot fit a slope")

    slope, intercept = np.polyfit(x, y, 1)
    fitted = intercept + slope * x
    residuals = y - fitted
    dof = n - 2

    if dof >= 1:
        residual_sd = float(math.sqrt(float(np.sum(residuals**2)) / dof))
        slope_stderr = residual_sd / math.sqrt(x_sum_squares) if x_sum_squares > 0 else None
        total_sum_squares = float(np.sum((y - float(np.mean(y))) ** 2))
        r_squared = 1.0 - (float(np.sum(residuals**2)) / total_sum_squares) if total_sum_squares > 0 else None
    else:
        # Two points define a line with zero residual. Reporting R squared of
        # 1.0 here would imply a certainty the data does not contain.
        residual_sd = None
        slope_stderr = None
        r_squared = None

    return TrajectoryFit(
        intercept=float(intercept),
        slope_per_year=float(slope),
        slope_stderr=slope_stderr,
        r_squared=r_squared,
        residual_sd=residual_sd,
        degrees_of_freedom=max(dof, 0),
        x_values=x,
        x_mean=x_mean,
        x_sum_squares=x_sum_squares,
        baseline_date=baseline_date,
        site=site,
        n=n,
        is_inferred_from_population=False,
    )


def bmd_percent_change_per_year(scans: list[ScanObservation], site: SkeletalSite) -> float | None:
    """Annualised BMD change as a percentage of the first measurement.

    Clinicians track percent BMD change, not T-score change, when judging
    treatment response, so both numbers appear on the timeline.
    """
    series = sorted(
        (s for s in scans if s.site == site and s.bmd_g_cm2 is not None),
        key=lambda s: s.scan_date,
    )
    if len(series) < 2:
        return None

    x = np.array([_years_between(series[0].scan_date, s.scan_date) for s in series], dtype=float)
    y = np.array([s.bmd_g_cm2 for s in series], dtype=float)
    if float(np.sum((x - np.mean(x)) ** 2)) == 0.0:
        return None

    slope, _ = np.polyfit(x, y, 1)
    first = float(y[0])
    if first <= 0:
        return None
    return float(slope) / first * 100.0


def build_velocity_report(
    fit: TrajectoryFit,
    scans: list[ScanObservation],
    confidence: float = 0.95,
) -> VelocityReport:
    """Turn the fit into the number a physician acts on, with its uncertainty."""
    lsc = least_significant_change(fit.site)
    span_years = float(fit.x_values[-1] - fit.x_values[0]) if fit.n > 1 else 0.0
    total_change = abs(fit.slope_per_year * span_years)
    exceeds_lsc = total_change >= lsc

    ci_lower: float | None = None
    ci_upper: float | None = None
    if fit.slope_stderr is not None and fit.degrees_of_freedom >= 1:
        t_crit = float(stats.t.ppf(1.0 - (1.0 - confidence) / 2.0, fit.degrees_of_freedom))
        margin = t_crit * fit.slope_stderr
        ci_lower = fit.slope_per_year - margin
        ci_upper = fit.slope_per_year + margin

    # Direction is judged against the noise floor, not against zero. A slope of
    # -0.02 per year over three years has moved the patient less than the
    # scanner can resolve, and calling that "declining" would be false alarm.
    if fit.is_inferred_from_population:
        direction = TrendDirection.DECLINING
    elif not exceeds_lsc:
        direction = TrendDirection.STABLE
    elif fit.slope_per_year < 0:
        direction = TrendDirection.DECLINING
    else:
        direction = TrendDirection.IMPROVING

    interpretation = _interpret_velocity(fit, direction, exceeds_lsc, lsc, span_years)

    return VelocityReport(
        t_score_per_year=round(fit.slope_per_year, 4),
        bmd_percent_per_year=(
            round(v, 3) if (v := bmd_percent_change_per_year(scans, fit.site)) is not None else None
        ),
        standard_error=round(fit.slope_stderr, 4) if fit.slope_stderr is not None else None,
        ci_lower=round(ci_lower, 4) if ci_lower is not None else None,
        ci_upper=round(ci_upper, 4) if ci_upper is not None else None,
        r_squared=round(fit.r_squared, 4) if fit.r_squared is not None else None,
        direction=direction,
        exceeds_least_significant_change=exceeds_lsc,
        interpretation=interpretation,
    )


def _interpret_velocity(
    fit: TrajectoryFit,
    direction: TrendDirection,
    exceeds_lsc: bool,
    lsc: float,
    span_years: float,
) -> str:
    if fit.is_inferred_from_population:
        return (
            "Only one scan on file. The line shown uses the population average rate of "
            "loss for this age and sex, not a measured trend. A second scan is needed "
            "before any velocity claim is clinically meaningful."
        )

    rate = abs(fit.slope_per_year)
    if not exceeds_lsc:
        return (
            f"Total change across {span_years:.1f} years sits inside the least significant "
            f"change for this site ({lsc:.2f} T-score units). Treat as stable."
        )

    if direction is TrendDirection.IMPROVING:
        return (
            f"Gaining {rate:.3f} T-score units per year across {span_years:.1f} years, "
            "a change larger than scanner precision error. Consistent with treatment response."
        )

    speed = "rapid" if rate >= 0.10 else "steady"
    return (
        f"Losing {rate:.3f} T-score units per year across {span_years:.1f} years. "
        f"This is a {speed} decline that exceeds scanner precision error."
    )


def project_points(
    fit: TrajectoryFit,
    scans: list[ScanObservation],
    projection_years: int,
    confidence: float = 0.95,
    steps_per_year: int = 2,
) -> list[TrajectoryPoint]:
    """Historical points as measured, then the forward projection.

    Historical points carry the observed T-score rather than the fitted value.
    A physician comparing the chart against the source PDF must see the number
    that is actually printed on the report.
    """
    series = sorted((s for s in scans if s.site == fit.site), key=lambda s: s.scan_date)
    points: list[TrajectoryPoint] = []

    for scan in series:
        years = _years_between(fit.baseline_date, scan.scan_date)
        points.append(
            TrajectoryPoint(
                date=scan.scan_date,
                years_from_baseline=round(years, 4),
                t_score=round(scan.t_score, 3),
                is_projected=False,
                band=classify_band(scan.t_score),
            )
        )

    last_observed_years = float(fit.x_values[-1])
    total_steps = projection_years * steps_per_year
    for step in range(1, total_steps + 1):
        years = last_observed_years + (step / steps_per_year)
        value = fit.value_at(years)
        interval = fit.prediction_interval(years, confidence=confidence)
        points.append(
            TrajectoryPoint(
                date=_date_at_offset(fit.baseline_date, years),
                years_from_baseline=round(years, 4),
                t_score=round(value, 3),
                is_projected=True,
                ci_lower=round(interval[0], 3) if interval else None,
                ci_upper=round(interval[1], 3) if interval else None,
                band=classify_band(value),
            )
        )

    return points


def describe_model_quality(fit: TrajectoryFit, scans: list[ScanObservation]) -> str:
    """Plain statement of how much the projection can be trusted.

    Surfaced verbatim in the UI. A projection whose limits are hidden is worse
    than no projection at all.
    """
    if fit.is_inferred_from_population:
        return "population_prior_single_scan"

    span = float(fit.x_values[-1] - fit.x_values[0])
    vendors = {s.manufacturer for s in scans if s.site == fit.site}
    mixed_vendor = len(vendors - {Manufacturer.UNKNOWN}) > 1

    if mixed_vendor:
        return "mixed_manufacturer_series_interpret_with_caution"
    if fit.n >= 4 and span >= 5.0:
        return "strong_four_or_more_scans_over_five_years"
    if fit.n >= 3 and span >= 3.0:
        return "moderate_three_scans_over_three_years"
    if fit.n == 2:
        return "weak_two_scans_no_error_estimate"
    return "limited_short_observation_window"
