from datetime import date

import pytest

from app.engine import trajectory_model as tm
from app.schemas import DiagnosticBand, Manufacturer, ScanObservation, Sex, SkeletalSite, TrendDirection


def scan(
    year: int,
    t_score: float,
    bmd: float | None = None,
    site=SkeletalSite.FEMORAL_NECK,
    manufacturer=Manufacturer.HOLOGIC,
) -> ScanObservation:
    return ScanObservation(
        scan_date=date(year, 6, 1),
        t_score=t_score,
        bmd_g_cm2=bmd,
        site=site,
        manufacturer=manufacturer,
    )


class TestBandClassification:
    @pytest.mark.parametrize(
        "t_score,expected",
        [
            (0.5, DiagnosticBand.NORMAL),
            (-0.99, DiagnosticBand.NORMAL),
            (-1.0, DiagnosticBand.OSTEOPENIA),
            (-2.49, DiagnosticBand.OSTEOPENIA),
            (-2.5, DiagnosticBand.OSTEOPOROSIS),
            (-3.4, DiagnosticBand.OSTEOPOROSIS),
        ],
    )
    def test_who_cutoffs(self, t_score, expected):
        assert tm.classify_band(t_score) == expected


class TestLeastSignificantChange:
    def test_femoral_neck_lsc_in_expected_clinical_range(self):
        lsc = tm.least_significant_change(SkeletalSite.FEMORAL_NECK)
        # Published femoral neck LSC lands near 0.3 T-score units.
        assert 0.2 < lsc < 0.45

    def test_every_site_has_a_reference(self):
        for site in SkeletalSite:
            assert tm.least_significant_change(site) > 0


class TestFit:
    def test_recovers_a_known_slope(self):
        scans = [scan(2014, -1.0), scan(2018, -1.4), scan(2022, -1.8)]
        fit = tm.fit_trajectory(scans, SkeletalSite.FEMORAL_NECK, Sex.FEMALE)
        assert fit.slope_per_year == pytest.approx(-0.1, abs=0.005)
        assert fit.n == 3
        assert not fit.is_inferred_from_population

    def test_single_scan_uses_population_prior(self):
        fit = tm.fit_trajectory([scan(2022, -1.5)], SkeletalSite.FEMORAL_NECK, Sex.FEMALE)
        assert fit.is_inferred_from_population
        assert fit.slope_per_year == tm.POPULATION_ANNUAL_T_CHANGE[Sex.FEMALE]
        assert fit.r_squared is None

    def test_two_scans_give_no_error_estimate(self):
        fit = tm.fit_trajectory([scan(2018, -1.0), scan(2022, -1.6)], SkeletalSite.FEMORAL_NECK, Sex.FEMALE)
        assert fit.slope_per_year == pytest.approx(-0.15, abs=0.005)
        assert fit.slope_stderr is None
        # Two points always fit a line perfectly. Claiming R squared of 1.0
        # would report certainty the data does not contain.
        assert fit.r_squared is None

    def test_rejects_site_with_no_scans(self):
        with pytest.raises(ValueError, match="no scans at site"):
            tm.fit_trajectory([scan(2022, -1.5)], SkeletalSite.LUMBAR_SPINE, Sex.FEMALE)

    def test_filters_to_the_requested_site(self):
        scans = [
            scan(2016, -1.0, site=SkeletalSite.FEMORAL_NECK),
            scan(2020, -1.4, site=SkeletalSite.FEMORAL_NECK),
            scan(2016, -3.0, site=SkeletalSite.LUMBAR_SPINE),
            scan(2020, -3.0, site=SkeletalSite.LUMBAR_SPINE),
        ]
        fit = tm.fit_trajectory(scans, SkeletalSite.FEMORAL_NECK, Sex.FEMALE)
        assert fit.n == 2
        assert fit.slope_per_year < 0


class TestVelocityReport:
    def test_declining_series_flagged_as_declining(self):
        scans = [scan(2012, -0.8), scan(2016, -1.3), scan(2020, -1.9), scan(2024, -2.4)]
        fit = tm.fit_trajectory(scans, SkeletalSite.FEMORAL_NECK, Sex.FEMALE)
        velocity = tm.build_velocity_report(fit, scans)
        assert velocity.direction is TrendDirection.DECLINING
        assert velocity.exceeds_least_significant_change
        assert velocity.t_score_per_year < 0
        assert velocity.ci_lower is not None and velocity.ci_upper is not None

    def test_change_inside_noise_floor_reads_as_stable(self):
        # 0.05 total drift across six years is well under the roughly 0.3
        # T-score LSC at the femoral neck.
        scans = [scan(2016, -1.50), scan(2019, -1.52), scan(2022, -1.55)]
        fit = tm.fit_trajectory(scans, SkeletalSite.FEMORAL_NECK, Sex.FEMALE)
        velocity = tm.build_velocity_report(fit, scans)
        assert velocity.direction is TrendDirection.STABLE
        assert not velocity.exceeds_least_significant_change
        assert "inside the least significant change" in velocity.interpretation

    def test_gaining_series_flagged_as_improving(self):
        scans = [scan(2018, -2.6), scan(2021, -2.3), scan(2024, -2.0)]
        fit = tm.fit_trajectory(scans, SkeletalSite.FEMORAL_NECK, Sex.FEMALE)
        velocity = tm.build_velocity_report(fit, scans)
        assert velocity.direction is TrendDirection.IMPROVING

    def test_bmd_percent_change_computed_when_bmd_present(self):
        scans = [scan(2018, -1.0, bmd=0.800), scan(2022, -1.4, bmd=0.768)]
        fit = tm.fit_trajectory(scans, SkeletalSite.FEMORAL_NECK, Sex.FEMALE)
        velocity = tm.build_velocity_report(fit, scans)
        # 4 percent loss across 4 years is 1 percent a year.
        assert velocity.bmd_percent_per_year == pytest.approx(-1.0, abs=0.05)

    def test_bmd_percent_change_absent_when_bmd_missing(self):
        scans = [scan(2018, -1.0), scan(2022, -1.4)]
        fit = tm.fit_trajectory(scans, SkeletalSite.FEMORAL_NECK, Sex.FEMALE)
        assert tm.build_velocity_report(fit, scans).bmd_percent_per_year is None


class TestProjection:
    def test_historical_points_carry_observed_values(self):
        scans = [scan(2016, -1.1), scan(2020, -1.6), scan(2024, -2.0)]
        fit = tm.fit_trajectory(scans, SkeletalSite.FEMORAL_NECK, Sex.FEMALE)
        points = tm.project_points(fit, scans, projection_years=10)

        historical = [p for p in points if not p.is_projected]
        assert [p.t_score for p in historical] == [-1.1, -1.6, -2.0]

    def test_projection_extends_the_requested_window(self):
        scans = [scan(2016, -1.1), scan(2020, -1.6), scan(2024, -2.0)]
        fit = tm.fit_trajectory(scans, SkeletalSite.FEMORAL_NECK, Sex.FEMALE)
        points = tm.project_points(fit, scans, projection_years=10, steps_per_year=2)

        projected = [p for p in points if p.is_projected]
        assert len(projected) == 20
        assert projected[-1].years_from_baseline == pytest.approx(18.0, abs=0.05)
        assert projected[-1].t_score < projected[0].t_score

    def test_prediction_interval_widens_with_distance(self):
        scans = [scan(2014, -1.0), scan(2018, -1.4), scan(2022, -1.7), scan(2024, -2.0)]
        fit = tm.fit_trajectory(scans, SkeletalSite.FEMORAL_NECK, Sex.FEMALE)
        points = [p for p in tm.project_points(fit, scans, 10) if p.is_projected]

        near = points[0]
        far = points[-1]
        assert near.ci_lower is not None and far.ci_lower is not None
        assert (far.ci_upper - far.ci_lower) > (near.ci_upper - near.ci_lower)

    def test_single_scan_projection_has_no_interval(self):
        scans = [scan(2022, -1.5)]
        fit = tm.fit_trajectory(scans, SkeletalSite.FEMORAL_NECK, Sex.FEMALE)
        projected = [p for p in tm.project_points(fit, scans, 10) if p.is_projected]
        assert all(p.ci_lower is None for p in projected)


class TestModelQuality:
    def test_strong_series(self):
        scans = [scan(2012, -0.9), scan(2016, -1.3), scan(2020, -1.7), scan(2024, -2.1)]
        fit = tm.fit_trajectory(scans, SkeletalSite.FEMORAL_NECK, Sex.FEMALE)
        assert tm.describe_model_quality(fit, scans) == "strong_four_or_more_scans_over_five_years"

    def test_mixed_manufacturer_series_is_called_out(self):
        scans = [
            scan(2014, -0.9, manufacturer=Manufacturer.HOLOGIC),
            scan(2018, -1.3, manufacturer=Manufacturer.GE_LUNAR),
            scan(2022, -1.7, manufacturer=Manufacturer.HOLOGIC),
            scan(2024, -1.9, manufacturer=Manufacturer.GE_LUNAR),
        ]
        fit = tm.fit_trajectory(scans, SkeletalSite.FEMORAL_NECK, Sex.FEMALE)
        assert tm.describe_model_quality(fit, scans) == "mixed_manufacturer_series_interpret_with_caution"

    def test_single_scan_quality(self):
        scans = [scan(2022, -1.5)]
        fit = tm.fit_trajectory(scans, SkeletalSite.FEMORAL_NECK, Sex.FEMALE)
        assert tm.describe_model_quality(fit, scans) == "population_prior_single_scan"
