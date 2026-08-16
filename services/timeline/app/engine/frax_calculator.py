"""Ten year fracture probability using the WHO FRAX input variable model.

WHAT THIS IS
------------
FRAX is the clinical standard for absolute fracture risk. It takes eleven
input variables and returns the ten year probability of a major osteoporotic
fracture (clinical spine, hip, forearm, or proximal humerus) and of hip
fracture specifically. Intervention guidelines around the world are written
against those two numbers, which is why Boensity anchors to FRAX rather than
inventing a score.

WHAT THIS IS NOT
----------------
The official FRAX regression coefficients and the country-specific
calibration tables held by the University of Sheffield are not published and
are not redistributable. This module is an independent implementation of the
same published methodology: a Poisson model of fracture hazard and competing
mortality hazard, with relative risks taken from the Kanis meta-analyses that
the FRAX papers describe.

It reproduces FRAX behaviour and ordering closely, but it is not the certified
FRAX tool and must not be presented as one. Any regulated deployment routes
the final number through a licensed FRAX API and uses this module for the
trajectory projection, where the value is in recomputing risk at future ages
rather than in the single point estimate. Every response carries a
calibration_note saying exactly that.

METHOD
------
For each year of the ten year window we hold a fracture hazard and a death
hazard. A patient who dies cannot go on to fracture, so ignoring competing
mortality would materially overstate risk in the over-eighties, which is the
population where the number matters most. Each year contributes

    P(fracture this year) = S(t) * (h_f / (h_f + h_d)) * (1 - exp(-(h_f + h_d)))

and survival carries forward. Summing across ten years gives the probability.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

from app.schemas import PatientContext, Sex

# ---------------------------------------------------------------------------
# Baseline epidemiology
# ---------------------------------------------------------------------------
# Annual incidence per 1000 person years at anchor ages, linearly interpolated
# between anchors. These are European reference rates. FRAX is calibrated per
# country because fracture incidence varies more than tenfold worldwide, so a
# deployment outside Europe must swap these tables. That is the single most
# important thing to change before clinical use, which is why they sit at the
# top of the file rather than buried in a config.

_MAJOR_OSTEOPOROTIC_INCIDENCE: dict[Sex, dict[int, float]] = {
    Sex.FEMALE: {50: 5.2, 55: 6.4, 60: 7.9, 65: 9.4, 70: 11.8, 75: 15.5, 80: 21.0, 85: 25.5, 90: 28.0},
    Sex.MALE: {50: 2.9, 55: 3.5, 60: 4.3, 65: 5.2, 70: 6.6, 75: 8.7, 80: 11.6, 85: 14.2, 90: 15.6},
}

_HIP_INCIDENCE: dict[Sex, dict[int, float]] = {
    Sex.FEMALE: {50: 0.13, 55: 0.20, 60: 0.40, 65: 0.75, 70: 1.40, 75: 2.55, 80: 4.20, 85: 7.20, 90: 11.0},
    Sex.MALE: {50: 0.09, 55: 0.14, 60: 0.25, 65: 0.45, 70: 0.80, 75: 1.53, 80: 2.73, 85: 4.70, 90: 7.00},
}

# Mean femoral neck T-score of the general population at each age.
#
# This table is what makes the BMD term correct, and leaving it out is the
# single easiest way to build a FRAX clone that reads high on every patient.
# The incidence rates above are population averages, which already contain the
# average bone density of that age group. An average 65 year old woman sits
# near T -1.3, not at T 0. Scoring her BMD relative risk against the young
# adult mean would charge her for bone loss that the baseline rate already
# counts, and every patient would come back looking worse than they are.
# Relative risk is therefore measured from this age-specific mean, so a patient
# at the average for their age gets a multiplier of exactly 1.0.
_MEAN_FEMORAL_NECK_T_SCORE: dict[Sex, dict[int, float]] = {
    Sex.FEMALE: {50: -0.7, 55: -0.9, 60: -1.1, 65: -1.3, 70: -1.5, 75: -1.7, 80: -1.9, 85: -2.1, 90: -2.2},
    Sex.MALE: {50: -0.5, 55: -0.6, 60: -0.8, 65: -0.9, 70: -1.1, 75: -1.3, 80: -1.5, 85: -1.7, 90: -1.8},
}

_MORTALITY: dict[Sex, dict[int, float]] = {
    Sex.FEMALE: {50: 2.5, 55: 3.8, 60: 6.0, 65: 9.5, 70: 15.0, 75: 26.0, 80: 45.0, 85: 80.0, 90: 140.0},
    Sex.MALE: {50: 4.0, 55: 6.2, 60: 10.0, 65: 16.0, 70: 25.0, 75: 42.0, 80: 70.0, 85: 120.0, 90: 200.0},
}


# ---------------------------------------------------------------------------
# Clinical risk factor relative risks
# ---------------------------------------------------------------------------
# From the Kanis meta-analyses underpinning FRAX. Each factor carries a
# separate RR for the major osteoporotic outcome and for hip, because they
# diverge sharply: smoking is a modest predictor of wrist fracture and a strong
# predictor of hip fracture.


@dataclass(frozen=True)
class RiskFactor:
    key: str
    label: str
    rr_major: float
    rr_hip: float
    # Fraction of the effect that runs through BMD. When a femoral neck
    # T-score is supplied, that portion is already counted by the BMD term and
    # applying it twice would double count. FRAX handles this by using a
    # separate attenuated coefficient set; the same correction in one number.
    bmd_mediated_fraction: float


_RISK_FACTORS: tuple[RiskFactor, ...] = (
    RiskFactor("previous_fracture", "Previous fragility fracture", 1.86, 1.62, 0.10),
    RiskFactor("parent_hip_fracture", "Parent hip fracture", 1.54, 2.27, 0.12),
    RiskFactor("current_smoking", "Current smoker", 1.29, 1.84, 0.25),
    RiskFactor("glucocorticoids", "Glucocorticoid exposure", 1.66, 2.13, 0.30),
    RiskFactor("rheumatoid_arthritis", "Rheumatoid arthritis", 1.95, 1.95, 0.20),
    RiskFactor("secondary_osteoporosis", "Secondary osteoporosis", 1.50, 1.60, 1.00),
    RiskFactor("alcohol_three_or_more_units", "Alcohol 3 or more units daily", 1.38, 1.68, 0.15),
)

# Gradient of risk per standard deviation decrease in femoral neck BMD.
# The hip gradient falls steeply with age: a low T-score at 50 is far more
# discriminating than the same T-score at 85, where competing frailty
# dominates. Ignoring that decay is the most common error in reimplementations.
_HIP_BMD_GRADIENT: dict[int, float] = {
    50: 3.68,
    55: 3.32,
    60: 2.98,
    65: 2.67,
    70: 2.38,
    75: 2.13,
    80: 1.91,
    85: 1.71,
    90: 1.60,
}
_MAJOR_BMD_GRADIENT: dict[int, float] = {
    50: 1.63,
    55: 1.60,
    60: 1.57,
    65: 1.53,
    70: 1.50,
    75: 1.46,
    80: 1.43,
    85: 1.40,
    90: 1.38,
}

# Fracture risk rises steeply below BMI 25 and falls modestly above it. Once a
# real BMD measurement is present most of the BMI signal is redundant, so it is
# damped rather than dropped.
_BMI_REFERENCE = 25.0
_BMI_RR_PER_UNIT_BELOW_HIP = 1.135
_BMI_RR_PER_UNIT_BELOW_MAJOR = 1.045
_BMI_RR_PER_UNIT_ABOVE_HIP = 0.965
_BMI_RR_PER_UNIT_ABOVE_MAJOR = 0.988
_BMI_DAMPING_WITH_BMD = 0.25

# Anti-fracture efficacy from the pivotal randomised trials, expressed as a
# hazard ratio on treatment.
_TREATMENT_EFFICACY: dict[str, dict[str, float | str]] = {
    "bisphosphonate": {
        "hr_major": 0.80,
        "hr_hip": 0.60,
        "label": "Oral bisphosphonate (alendronate class)",
        "note": (
            "Hazard ratios from the alendronate and risedronate fracture endpoint trials: "
            "roughly 20 percent reduction across the major osteoporotic composite and "
            "40 percent at the hip, in patients with existing low bone mass."
        ),
    },
    "denosumab": {
        "hr_major": 0.75,
        "hr_hip": 0.60,
        "label": "Denosumab",
        "note": (
            "Hazard ratios from the FREEDOM trial: 68 percent reduction in vertebral "
            "fracture, 40 percent at the hip, 20 percent non-vertebral, combined into a "
            "composite major osteoporotic figure."
        ),
    },
    "none": {
        "hr_major": 1.0,
        "hr_hip": 1.0,
        "label": "No pharmacological treatment",
        "note": "Reference arm.",
    },
}

# BHOF / National Osteoporosis Foundation intervention thresholds.
INTERVENTION_THRESHOLD_MAJOR = 20.0
INTERVENTION_THRESHOLD_HIP = 3.0

CALIBRATION_NOTE = (
    "Independent implementation of the published FRAX methodology using European "
    "reference incidence. Not the certified University of Sheffield FRAX tool and not "
    "calibrated to a specific national cohort. Use for trajectory comparison and "
    "triage. Confirm any treatment decision against a licensed FRAX calculation."
)


# ---------------------------------------------------------------------------
# Interpolation helpers
# ---------------------------------------------------------------------------


def _interpolate(table: dict[int, float], age: float) -> float:
    """Linear interpolation across anchor ages, clamped at the table edges."""
    anchors = sorted(table)
    if age <= anchors[0]:
        return table[anchors[0]]
    if age >= anchors[-1]:
        return table[anchors[-1]]

    for lower, upper in itertools.pairwise(anchors):
        if lower <= age <= upper:
            span = upper - lower
            weight = (age - lower) / span
            return table[lower] + weight * (table[upper] - table[lower])

    return table[anchors[-1]]


def _annual_hazard(table: dict[Sex, dict[int, float]], sex: Sex, age: float) -> float:
    """Convert incidence per 1000 person years into a continuous hazard.

    Using -ln(1 - p) rather than p itself keeps the arithmetic correct at the
    high rates seen in the over-eighties, where the two differ enough to move
    a patient across an intervention threshold.
    """
    rate = _interpolate(table[sex], age) / 1000.0
    rate = min(rate, 0.999)
    return -math.log(1.0 - rate)


# ---------------------------------------------------------------------------
# Relative risk assembly
# ---------------------------------------------------------------------------


@dataclass
class RelativeRisks:
    major: float
    hip: float
    contributions: list[str] = field(default_factory=list)


def _bmi_relative_risk(bmi: float, bmd_present: bool) -> tuple[float, float]:
    if bmi < _BMI_REFERENCE:
        units = _BMI_REFERENCE - bmi
        rr_hip = _BMI_RR_PER_UNIT_BELOW_HIP**units
        rr_major = _BMI_RR_PER_UNIT_BELOW_MAJOR**units
    else:
        units = bmi - _BMI_REFERENCE
        rr_hip = _BMI_RR_PER_UNIT_ABOVE_HIP**units
        rr_major = _BMI_RR_PER_UNIT_ABOVE_MAJOR**units

    if bmd_present:
        rr_hip = rr_hip**_BMI_DAMPING_WITH_BMD
        rr_major = rr_major**_BMI_DAMPING_WITH_BMD

    # BMI cannot rescue a patient indefinitely; the protective tail flattens.
    return max(rr_major, 0.70), max(rr_hip, 0.55)


def _bmd_relative_risk(t_score: float, age: float, sex: Sex) -> tuple[float, float]:
    """RR from femoral neck T-score, using age-specific gradients of risk.

    Measured as standard deviations below the population mean for this age and
    sex, not below the young adult mean. See _MEAN_FEMORAL_NECK_T_SCORE.
    """
    # Beyond these bounds the source cohorts hold almost no data, and the
    # exponential would run away into risks nobody has ever observed.
    clamped = max(min(t_score, 2.0), -4.0)
    population_mean = _interpolate(_MEAN_FEMORAL_NECK_T_SCORE[sex], age)
    sd_below_peers = population_mean - clamped

    gradient_hip = _interpolate(_HIP_BMD_GRADIENT, age)
    gradient_major = _interpolate(_MAJOR_BMD_GRADIENT, age)
    return gradient_major**sd_below_peers, gradient_hip**sd_below_peers


def assemble_relative_risks(
    patient: PatientContext,
    femoral_neck_t_score: float | None,
) -> RelativeRisks:
    bmd_present = femoral_neck_t_score is not None
    rr_major = 1.0
    rr_hip = 1.0
    contributions: list[tuple[float, str]] = []

    for factor in _RISK_FACTORS:
        if not getattr(patient, factor.key):
            continue

        # Secondary osteoporosis is entirely BMD mediated by construction, so
        # FRAX drops it once a measurement exists. The attenuation formula
        # below reduces it to exactly 1.0 in that case.
        if bmd_present:
            attenuation = 1.0 - factor.bmd_mediated_fraction
            effective_major = factor.rr_major**attenuation
            effective_hip = factor.rr_hip**attenuation
        else:
            effective_major = factor.rr_major
            effective_hip = factor.rr_hip

        rr_major *= effective_major
        rr_hip *= effective_hip
        if effective_major > 1.01 or effective_hip > 1.01:
            contributions.append((max(effective_major, effective_hip), factor.label))

    bmi_major, bmi_hip = _bmi_relative_risk(patient.bmi, bmd_present)
    rr_major *= bmi_major
    rr_hip *= bmi_hip
    if patient.bmi < 20.0:
        contributions.append((bmi_hip, f"Low BMI ({patient.bmi:.1f})"))

    if femoral_neck_t_score is not None:
        bmd_major, bmd_hip = _bmd_relative_risk(femoral_neck_t_score, patient.age, patient.sex)
        rr_major *= bmd_major
        rr_hip *= bmd_hip
        if femoral_neck_t_score < -1.0:
            contributions.append((bmd_hip, f"Femoral neck T-score {femoral_neck_t_score:.1f}"))

    contributions.sort(key=lambda pair: pair[0], reverse=True)
    return RelativeRisks(
        major=rr_major,
        hip=rr_hip,
        contributions=[label for _, label in contributions],
    )


# ---------------------------------------------------------------------------
# Probability
# ---------------------------------------------------------------------------


def _ten_year_probability(
    sex: Sex,
    age: float,
    incidence: dict[Sex, dict[int, float]],
    relative_risk: float,
    years: int = 10,
) -> float:
    """Cumulative incidence with death as a competing risk."""
    survival = 1.0
    probability = 0.0

    for offset in range(years):
        # Midpoint of the year, so a ten year window centres correctly rather
        # than being biased a full year young.
        current_age = age + offset + 0.5
        hazard_fracture = _annual_hazard(incidence, sex, current_age) * relative_risk
        hazard_death = _annual_hazard(_MORTALITY, sex, current_age)
        hazard_total = hazard_fracture + hazard_death

        if hazard_total <= 0.0:
            continue

        any_event = 1.0 - math.exp(-hazard_total)
        probability += survival * any_event * (hazard_fracture / hazard_total)
        survival *= math.exp(-hazard_total)

    return min(probability, 0.999)


@dataclass
class FraxResult:
    major_osteoporotic_percent: float
    hip_percent: float
    bmd_included: bool
    contributing_factors: list[str]
    relative_risk_major: float
    relative_risk_hip: float


def calculate_frax(
    patient: PatientContext,
    femoral_neck_t_score: float | None = None,
    years: int = 10,
) -> FraxResult:
    """Ten year probability of major osteoporotic fracture and of hip fracture."""
    risks = assemble_relative_risks(patient, femoral_neck_t_score)

    major = _ten_year_probability(patient.sex, patient.age, _MAJOR_OSTEOPOROTIC_INCIDENCE, risks.major, years)
    hip = _ten_year_probability(patient.sex, patient.age, _HIP_INCIDENCE, risks.hip, years)

    # Hip fracture is a subset of the major osteoporotic composite, so a hip
    # probability above the composite is arithmetically impossible. Extreme
    # inputs can push the two RR chains apart far enough to produce it.
    hip = min(hip, major)

    return FraxResult(
        major_osteoporotic_percent=round(major * 100.0, 1),
        hip_percent=round(hip * 100.0, 1),
        bmd_included=femoral_neck_t_score is not None,
        contributing_factors=risks.contributions,
        relative_risk_major=round(risks.major, 3),
        relative_risk_hip=round(risks.hip, 3),
    )


def apply_treatment(result: FraxResult, agent: str = "bisphosphonate") -> dict[str, float | str]:
    """Recompute probability under treatment.

    Returned alongside the untreated number because that pair is the whole
    physician argument. Percent reductions are abstract. Two absolute
    probabilities side by side are a decision.
    """
    profile = _TREATMENT_EFFICACY.get(agent, _TREATMENT_EFFICACY["bisphosphonate"])
    hr_major = float(profile["hr_major"])
    hr_hip = float(profile["hr_hip"])

    treated_major = round(result.major_osteoporotic_percent * hr_major, 1)
    treated_hip = round(result.hip_percent * hr_hip, 1)
    arr_major = round(result.major_osteoporotic_percent - treated_major, 1)
    arr_hip = round(result.hip_percent - treated_hip, 1)

    nnt = int(round(100.0 / arr_major)) if arr_major > 0 else None

    return {
        "agent": str(profile["label"]),
        "major_osteoporotic_percent": treated_major,
        "hip_percent": treated_hip,
        "absolute_risk_reduction_major": arr_major,
        "absolute_risk_reduction_hip": arr_hip,
        "number_needed_to_treat_major": nnt,
        "evidence_note": str(profile["note"]),
    }


def meets_intervention_threshold(result: FraxResult) -> tuple[bool, str]:
    """BHOF thresholds. Returned with the basis so the UI never shows a bare
    boolean a physician cannot audit."""
    major_hit = result.major_osteoporotic_percent >= INTERVENTION_THRESHOLD_MAJOR
    hip_hit = result.hip_percent >= INTERVENTION_THRESHOLD_HIP

    if major_hit and hip_hit:
        basis = (
            f"Both thresholds met: major osteoporotic {result.major_osteoporotic_percent}% "
            f"at or above {INTERVENTION_THRESHOLD_MAJOR}%, hip {result.hip_percent}% "
            f"at or above {INTERVENTION_THRESHOLD_HIP}%."
        )
    elif major_hit:
        basis = (
            f"Major osteoporotic probability {result.major_osteoporotic_percent}% is at or "
            f"above the {INTERVENTION_THRESHOLD_MAJOR}% treatment threshold."
        )
    elif hip_hit:
        basis = (
            f"Hip probability {result.hip_percent}% is at or above the "
            f"{INTERVENTION_THRESHOLD_HIP}% treatment threshold."
        )
    else:
        basis = (
            f"Below both thresholds (major osteoporotic {INTERVENTION_THRESHOLD_MAJOR}%, "
            f"hip {INTERVENTION_THRESHOLD_HIP}%). Reassess at the next scan interval."
        )

    return (major_hit or hip_hit), basis
