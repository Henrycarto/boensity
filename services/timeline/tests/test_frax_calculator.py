"""FRAX behaviour tests.

These assert direction, ordering, and clinical plausibility rather than exact
equality against the certified tool. This is an independent implementation of
the published methodology, so pinning to Sheffield's decimals would be a false
claim. What must hold is that risk moves the right way for every input and
lands in the range published cohorts report.
"""

import pytest

from app.engine import frax_calculator as fc
from app.schemas import PatientContext, Sex


def patient(**overrides) -> PatientContext:
    base = {
        "patient_id": "test-001",
        "age": 65.0,
        "sex": Sex.FEMALE,
        "weight_kg": 65.0,
        "height_cm": 165.0,
    }
    base.update(overrides)
    return PatientContext(**base)


class TestBaseline:
    def test_healthy_65_year_old_woman_lands_in_published_range(self):
        # Published FRAX for a 65 year old woman, BMI near 24, no risk factors
        # and no BMD reports roughly 11 percent major osteoporotic and just
        # over 1 percent hip. Staying inside a band around that is the check
        # that the incidence tables have not drifted.
        result = fc.calculate_frax(patient())
        assert 8.0 < result.major_osteoporotic_percent < 15.0
        assert 0.5 < result.hip_percent < 4.0

    def test_average_bmd_for_age_does_not_change_the_estimate(self):
        """A patient exactly at the population mean T-score for their age
        should score the same with or without a BMD measurement. If supplying
        an average measurement moves the number, the BMD term is not centred."""
        without_bmd = fc.calculate_frax(patient(age=65.0))
        at_population_mean = fc.calculate_frax(patient(age=65.0), -1.3)
        assert at_population_mean.major_osteoporotic_percent == pytest.approx(
            without_bmd.major_osteoporotic_percent, rel=0.15
        )

    def test_better_than_average_bone_lowers_risk(self):
        average = fc.calculate_frax(patient(age=70.0), -1.5)
        strong = fc.calculate_frax(patient(age=70.0), 0.0)
        assert strong.hip_percent < average.hip_percent

    def test_risk_rises_with_age(self):
        young = fc.calculate_frax(patient(age=50.0))
        old = fc.calculate_frax(patient(age=80.0))
        assert old.major_osteoporotic_percent > young.major_osteoporotic_percent
        assert old.hip_percent > young.hip_percent

    def test_women_carry_higher_risk_than_men_at_the_same_age(self):
        female = fc.calculate_frax(patient(sex=Sex.FEMALE))
        male = fc.calculate_frax(patient(sex=Sex.MALE))
        assert female.major_osteoporotic_percent > male.major_osteoporotic_percent

    def test_hip_never_exceeds_the_major_composite(self):
        # Hip fracture is a member of the composite, so this is an identity
        # the model must never violate, including at extreme inputs.
        for age in (50.0, 65.0, 80.0, 90.0):
            for t in (None, 0.0, -2.5, -4.0):
                result = fc.calculate_frax(
                    patient(age=age, previous_fracture=True, glucocorticoids=True, current_smoking=True), t
                )
                assert result.hip_percent <= result.major_osteoporotic_percent


class TestCalibration:
    """Regression guard against the reference curve.

    These are the published FRAX ten year probabilities for a woman with no
    clinical risk factors, BMI near 24, and no BMD entered. The tables in this
    module were fitted to reproduce them, so this test is what catches an
    incidence edit that silently moves every patient in the system.
    """

    REFERENCE = {
        50: (7.0, 0.6),
        55: (8.0, 0.8),
        60: (9.5, 1.0),
        65: (11.0, 1.4),
        70: (13.0, 2.3),
        75: (15.0, 3.6),
        80: (16.0, 5.0),
        85: (16.0, 6.0),
    }

    @pytest.mark.parametrize("age", sorted(REFERENCE))
    def test_tracks_published_reference_within_two_points(self, age):
        expected_major, expected_hip = self.REFERENCE[age]
        result = fc.calculate_frax(patient(age=float(age)))
        assert result.major_osteoporotic_percent == pytest.approx(expected_major, abs=2.0)
        assert result.hip_percent == pytest.approx(expected_hip, abs=2.0)


class TestRiskFactors:
    @pytest.mark.parametrize(
        "factor",
        [
            "previous_fracture",
            "parent_hip_fracture",
            "current_smoking",
            "glucocorticoids",
            "rheumatoid_arthritis",
            "alcohol_three_or_more_units",
        ],
    )
    def test_each_factor_raises_risk(self, factor):
        without = fc.calculate_frax(patient())
        with_factor = fc.calculate_frax(patient(**{factor: True}))
        assert with_factor.major_osteoporotic_percent > without.major_osteoporotic_percent

    def test_factors_compound(self):
        one = fc.calculate_frax(patient(previous_fracture=True))
        several = fc.calculate_frax(
            patient(previous_fracture=True, parent_hip_fracture=True, current_smoking=True)
        )
        assert several.major_osteoporotic_percent > one.major_osteoporotic_percent

    def test_secondary_osteoporosis_drops_out_when_bmd_is_supplied(self):
        # FRAX treats secondary osteoporosis as acting entirely through BMD.
        # With a measurement in hand, counting it again would double count.
        with_flag = fc.calculate_frax(patient(secondary_osteoporosis=True), -2.0)
        without_flag = fc.calculate_frax(patient(), -2.0)
        assert with_flag.major_osteoporotic_percent == pytest.approx(
            without_flag.major_osteoporotic_percent, abs=0.15
        )

    def test_secondary_osteoporosis_counts_without_bmd(self):
        with_flag = fc.calculate_frax(patient(secondary_osteoporosis=True))
        without_flag = fc.calculate_frax(patient())
        assert with_flag.major_osteoporotic_percent > without_flag.major_osteoporotic_percent

    def test_low_bmi_raises_risk(self):
        thin = fc.calculate_frax(patient(weight_kg=45.0))
        average = fc.calculate_frax(patient(weight_kg=65.0))
        assert thin.hip_percent > average.hip_percent

    def test_contributing_factors_are_reported_and_ranked(self):
        result = fc.calculate_frax(patient(previous_fracture=True, glucocorticoids=True), -3.0)
        assert result.contributing_factors
        assert any("T-score" in f for f in result.contributing_factors)


class TestBmdEffect:
    def test_lower_t_score_raises_risk(self):
        normal = fc.calculate_frax(patient(), 0.0)
        osteopenic = fc.calculate_frax(patient(), -1.8)
        osteoporotic = fc.calculate_frax(patient(), -3.0)
        assert osteoporotic.hip_percent > osteopenic.hip_percent > normal.hip_percent

    def test_bmd_inclusion_is_reported(self):
        assert fc.calculate_frax(patient(), -2.0).bmd_included
        assert not fc.calculate_frax(patient()).bmd_included

    def test_bmd_gradient_weakens_with_age(self):
        """A low T-score discriminates far more sharply at 50 than at 85."""
        young_ratio = (
            fc.calculate_frax(patient(age=50.0), -3.0).hip_percent
            / fc.calculate_frax(patient(age=50.0), 0.0).hip_percent
        )
        old_ratio = (
            fc.calculate_frax(patient(age=85.0), -3.0).hip_percent
            / fc.calculate_frax(patient(age=85.0), 0.0).hip_percent
        )
        assert young_ratio > old_ratio

    def test_extreme_t_score_is_clamped_not_extrapolated(self):
        very_low = fc.calculate_frax(patient(), -6.0)
        clamped = fc.calculate_frax(patient(), -4.0)
        assert very_low.major_osteoporotic_percent == clamped.major_osteoporotic_percent


class TestCompetingMortality:
    def test_probability_stays_bounded_at_extreme_risk(self):
        result = fc.calculate_frax(
            patient(
                age=90.0,
                weight_kg=40.0,
                previous_fracture=True,
                parent_hip_fracture=True,
                current_smoking=True,
                glucocorticoids=True,
                rheumatoid_arthritis=True,
                alcohol_three_or_more_units=True,
            ),
            -4.0,
        )
        assert result.major_osteoporotic_percent <= 99.9
        assert result.hip_percent <= 99.9

    def test_mortality_flattens_risk_at_the_oldest_ages(self):
        """Fracture incidence climbs past 85 but so does death, so the ten
        year probability must not climb without limit."""
        at_85 = fc.calculate_frax(patient(age=85.0), -2.5).hip_percent
        at_90 = fc.calculate_frax(patient(age=90.0), -2.5).hip_percent
        assert at_90 < at_85 * 2.0


class TestTreatment:
    def test_treatment_lowers_both_numbers(self):
        result = fc.calculate_frax(patient(age=72.0, previous_fracture=True), -2.7)
        treated = fc.apply_treatment(result, "bisphosphonate")
        assert treated["major_osteoporotic_percent"] < result.major_osteoporotic_percent
        assert treated["hip_percent"] < result.hip_percent

    def test_absolute_risk_reduction_is_consistent(self):
        result = fc.calculate_frax(patient(age=72.0, previous_fracture=True), -2.7)
        treated = fc.apply_treatment(result)
        expected = round(result.major_osteoporotic_percent - treated["major_osteoporotic_percent"], 1)
        assert treated["absolute_risk_reduction_major"] == expected

    def test_number_needed_to_treat_is_sane(self):
        result = fc.calculate_frax(patient(age=75.0, previous_fracture=True), -3.0)
        treated = fc.apply_treatment(result)
        assert 1 <= treated["number_needed_to_treat_major"] <= 200

    def test_no_treatment_arm_changes_nothing(self):
        result = fc.calculate_frax(patient())
        treated = fc.apply_treatment(result, "none")
        assert treated["major_osteoporotic_percent"] == result.major_osteoporotic_percent

    def test_denosumab_profile_available(self):
        result = fc.calculate_frax(patient(age=72.0), -2.8)
        treated = fc.apply_treatment(result, "denosumab")
        assert "Denosumab" in treated["agent"]


class TestInterventionThreshold:
    def test_low_risk_patient_below_threshold(self):
        result = fc.calculate_frax(patient(age=55.0), -0.5)
        met, basis = fc.meets_intervention_threshold(result)
        assert not met
        assert "Below both thresholds" in basis

    def test_high_risk_patient_meets_threshold(self):
        result = fc.calculate_frax(
            patient(age=78.0, weight_kg=50.0, previous_fracture=True, parent_hip_fracture=True),
            -3.2,
        )
        met, basis = fc.meets_intervention_threshold(result)
        assert met
        assert "threshold" in basis.lower()

    def test_basis_string_is_always_populated(self):
        for age in (50.0, 70.0, 90.0):
            _, basis = fc.meets_intervention_threshold(fc.calculate_frax(patient(age=age)))
            assert basis
