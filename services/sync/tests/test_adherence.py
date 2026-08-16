"""Adherence scoring and referral routing tests."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.engine import adherence_scorer as scorer
from app.engine import referral_router
from app.engine.pharmacy_client import classify_drug
from app.main import app
from app.schemas import (
    AdherenceBand,
    DrugClass,
    FillRecord,
    ReferralCandidate,
    ReferralStatus,
)

TODAY = date(2026, 6, 1)
PERIOD_START = TODAY - timedelta(days=364)


def fill(days_ago: int, days_supply: int = 30, drug_class=DrugClass.ORAL_BISPHOSPHONATE) -> FillRecord:
    return FillRecord(
        fill_date=TODAY - timedelta(days=days_ago),
        days_supply=days_supply,
        drug_name="Alendronate 70mg",
        drug_class=drug_class,
    )


def monthly_fills(months: int = 12) -> list[FillRecord]:
    return [fill(30 * index) for index in range(months, 0, -1)]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


class TestProportionDaysCovered:
    def test_continuous_monthly_fills_score_near_one(self):
        pdc, covered, total = scorer.proportion_days_covered(monthly_fills(), PERIOD_START, TODAY)
        assert pdc >= 0.95
        assert covered <= total

    def test_no_fills_scores_zero(self):
        pdc, covered, _ = scorer.proportion_days_covered([], PERIOD_START, TODAY)
        assert pdc == 0.0
        assert covered == 0

    def test_overlapping_fills_do_not_double_count(self):
        """A patient refilling every 15 days on a 30 day supply is stockpiling,
        not achieving 200 percent adherence."""
        early_refills = [fill(15 * index) for index in range(24, 0, -1)]
        pdc, _, _ = scorer.proportion_days_covered(early_refills, PERIOD_START, TODAY)
        assert pdc <= 1.0

    def test_half_the_year_covered_scores_near_half(self):
        six_months = [fill(30 * index) for index in range(6, 0, -1)]
        pdc, _, _ = scorer.proportion_days_covered(six_months, PERIOD_START, TODAY)
        assert 0.4 <= pdc <= 0.6


class TestInjectableHandling:
    def test_denosumab_days_supply_of_one_is_corrected(self):
        """Pharmacy feeds record a six month injection with days_supply 1.
        Taking that literally scores a perfectly adherent patient at 0.5
        percent coverage, which is the single most damaging bug available
        in this module."""
        injections = [
            FillRecord(
                fill_date=TODAY - timedelta(days=offset),
                days_supply=1,
                drug_name="Prolia",
                drug_class=DrugClass.DENOSUMAB,
            )
            for offset in (360, 180, 5)
        ]
        pdc, _, _ = scorer.proportion_days_covered(injections, PERIOD_START, TODAY)
        assert pdc >= 0.90

    def test_a_real_days_supply_on_an_injectable_is_respected(self):
        record = FillRecord(
            fill_date=TODAY,
            days_supply=180,
            drug_name="Prolia",
            drug_class=DrugClass.DENOSUMAB,
        )
        assert scorer._effective_days_supply(record) == 180

    def test_oral_days_supply_is_never_overridden(self):
        record = FillRecord(
            fill_date=TODAY,
            days_supply=7,
            drug_name="Alendronate",
            drug_class=DrugClass.ORAL_BISPHOSPHONATE,
        )
        assert scorer._effective_days_supply(record) == 7


class TestGapDetection:
    def test_no_gaps_in_a_continuous_series(self):
        gaps = scorer.find_gaps(monthly_fills(), PERIOD_START, TODAY)
        assert not [gap for gap in gaps if gap.is_material]

    def test_a_long_gap_is_found_and_measured(self):
        with_gap = [fill(offset) for offset in (360, 330, 300, 90, 60, 30)]
        gaps = [gap for gap in scorer.find_gaps(with_gap, PERIOD_START, TODAY) if gap.is_material]
        assert len(gaps) == 1
        assert gaps[0].days >= 150

    def test_a_short_late_refill_is_not_material(self):
        slightly_late = [fill(offset) for offset in (350, 315, 280, 245, 210, 175, 140, 105, 70, 35, 3)]
        material = [gap for gap in scorer.find_gaps(slightly_late, PERIOD_START, TODAY) if gap.is_material]
        assert not material

    def test_no_fills_at_all_is_one_whole_gap(self):
        gaps = scorer.find_gaps([], PERIOD_START, TODAY)
        assert len(gaps) == 1
        assert gaps[0].is_material


class TestBanding:
    def test_adherent_patient(self):
        result = scorer.score_adherence("pt-1", monthly_fills(), PERIOD_START, TODAY, today=TODAY)
        assert result.band is AdherenceBand.ADHERENT
        assert result.proportion_days_covered >= scorer.PDC_ADHERENT
        assert "at or above" in result.interpretation

    def test_partial_patient(self):
        partial = [fill(offset) for offset in (360, 330, 300, 270, 120, 90, 60, 30)]
        result = scorer.score_adherence("pt-2", partial, PERIOD_START, TODAY, today=TODAY)
        assert result.band is AdherenceBand.PARTIAL

    def test_lapsed_patient_overrides_a_good_coverage_number(self):
        """A patient can have high coverage across the year and still have
        stopped four months ago. Recency has to win, or the panel reports
        someone as adherent who is no longer taking anything."""
        stopped = [fill(offset) for offset in (360, 330, 300, 270, 240, 210, 180, 150, 120)]
        result = scorer.score_adherence("pt-3", stopped, PERIOD_START, TODAY, today=TODAY)
        assert result.days_since_last_fill >= scorer.LAPSED_DAYS
        assert result.band is AdherenceBand.LAPSED

    def test_no_fills_is_reported_clearly(self):
        result = scorer.score_adherence("pt-4", [], PERIOD_START, TODAY, today=TODAY)
        assert result.fill_count == 0
        assert "No fills recorded" in result.interpretation


class TestMedicationPossessionRatio:
    def test_mpr_can_exceed_one_when_pdc_cannot(self):
        """The divergence is the signal: refilling but not taking."""
        stockpiling = [fill(15 * index) for index in range(24, 0, -1)]
        mpr = scorer.medication_possession_ratio(stockpiling, PERIOD_START, TODAY)
        pdc, _, _ = scorer.proportion_days_covered(stockpiling, PERIOD_START, TODAY)
        assert mpr > 1.0
        assert pdc <= 1.0


class TestPrivateScoringBoundary:
    def test_risk_score_is_absent_without_the_private_package(self):
        """The private package is not in this repository, so the composite
        score must come back None and be labelled unavailable rather than
        defaulting to zero, which would read as no risk."""
        result = scorer.score_adherence("pt-5", monthly_fills(), PERIOD_START, TODAY, today=TODAY)
        assert result.risk_score is None
        assert result.risk_score_available is False

    def test_everything_else_still_computes(self):
        result = scorer.score_adherence("pt-6", monthly_fills(), PERIOD_START, TODAY, today=TODAY)
        assert result.proportion_days_covered > 0
        assert result.medication_possession_ratio > 0
        assert result.band is not None
        assert result.interpretation


class TestDrugClassification:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("Alendronate Sodium 70mg", DrugClass.ORAL_BISPHOSPHONATE),
            ("FOSAMAX 70 MG TABLET", DrugClass.ORAL_BISPHOSPHONATE),
            ("Prolia 60mg/mL syringe", DrugClass.DENOSUMAB),
            ("Reclast (zoledronic acid)", DrugClass.IV_BISPHOSPHONATE),
            ("Evista 60mg", DrugClass.SERM),
            ("Forteo pen", DrugClass.ANABOLIC),
            ("Cholecalciferol 1000 IU", DrugClass.SUPPLEMENT),
        ],
    )
    def test_known_drugs_map_to_their_class(self, name, expected):
        assert classify_drug(name) == expected

    def test_unknown_drug_falls_back_to_the_commonest_class(self):
        assert classify_drug("Something Unlisted 10mg") is DrugClass.ORAL_BISPHOSPHONATE


class TestReferralRouting:
    def _score(self, fills, patient_id="pt-r"):
        return scorer.score_adherence(patient_id, fills, PERIOD_START, TODAY, today=TODAY)

    def test_adherent_oral_patient_is_not_referred(self):
        decision = referral_router.route(self._score(monthly_fills()), [])
        assert decision.status is ReferralStatus.NOT_REQUIRED

    def test_lapsed_specialty_patient_is_referred(self):
        injections = [
            FillRecord(
                fill_date=TODAY - timedelta(days=offset),
                days_supply=1,
                drug_name="Prolia",
                drug_class=DrugClass.DENOSUMAB,
            )
            for offset in (540, 400)
        ]
        candidates = [
            ReferralCandidate(
                pharmacy_id="ph-1",
                pharmacy_name="Meridian Specialty",
                supports_drug_class=True,
                accepts_new_referrals=True,
                match_score_available=False,
            )
        ]
        decision = referral_router.route(self._score(injections), candidates)
        assert decision.status is ReferralStatus.SUGGESTED
        assert decision.candidates

    def test_very_low_coverage_oral_patient_is_not_referred(self):
        """Below 40 percent the barrier is tolerance or belief. Moving the
        patient to another pharmacy does not address either, and referring
        them anyway would be routing for its own sake."""
        barely = [fill(offset) for offset in (360, 330)]
        decision = referral_router.route(self._score(barely), [])
        assert decision.status is ReferralStatus.NOT_REQUIRED
        assert "prescriber review" in decision.reason

    def test_ineligible_pharmacies_are_filtered_out(self):
        candidates = [
            ReferralCandidate(
                pharmacy_id="ph-no-stock",
                pharmacy_name="Corner Chemist",
                supports_drug_class=False,
                accepts_new_referrals=True,
                match_score_available=False,
            ),
            ReferralCandidate(
                pharmacy_id="ph-closed",
                pharmacy_name="Coast Infusion",
                supports_drug_class=True,
                accepts_new_referrals=False,
                match_score_available=False,
            ),
        ]
        eligible = referral_router.filter_eligible(candidates, DrugClass.DENOSUMAB)
        assert eligible == []

    def test_no_pharmacy_is_preselected_without_private_ranking(self):
        """Ranking is the commercial layer and it is not in this repository.
        Presenting an arbitrary first candidate as a recommendation would
        pass off list order as a decision."""
        injections = [
            FillRecord(
                fill_date=TODAY - timedelta(days=offset),
                days_supply=1,
                drug_name="Prolia",
                drug_class=DrugClass.DENOSUMAB,
            )
            for offset in (540, 400)
        ]
        candidates = [
            ReferralCandidate(
                pharmacy_id=f"ph-{index}",
                pharmacy_name=f"Pharmacy {index}",
                supports_drug_class=True,
                accepts_new_referrals=True,
                match_score_available=False,
            )
            for index in range(3)
        ]
        decision = referral_router.route(self._score(injections), candidates)
        assert decision.routing_available is False
        assert decision.selected_pharmacy_id is None
        assert len(decision.candidates) == 3


class TestApi:
    def test_health_reports_every_degraded_capability(self, client):
        body = client.get("/health").json()
        assert set(body) == {"data", "error", "meta"}
        assert body["data"]["private_scoring_available"] is False
        assert body["data"]["private_routing_available"] is False
        assert body["data"]["live_pharmacy_connection"] is False

    def test_adherence_endpoint_scores_a_supplied_history(self, client):
        # Anchored to the real clock rather than the module's fixed TODAY.
        # The route reads date.today() to decide lapse, which is correct
        # behaviour, so a history ending months in the past would score as
        # lapsed no matter how complete it is.
        now = date.today()
        payload = {
            "patient_id": "pt-api-1",
            "fills": [
                {
                    "fill_date": (now - timedelta(days=30 * index)).isoformat(),
                    "days_supply": 30,
                    "drug_name": "Alendronate 70mg",
                    "drug_class": "oral_bisphosphonate",
                }
                for index in range(12, 0, -1)
            ],
            "period_start": (now - timedelta(days=364)).isoformat(),
            "period_end": now.isoformat(),
        }
        body = client.post("/adherence", json=payload).json()
        assert body["data"]["score"]["band"] == "adherent"
        assert body["data"]["interventions"] == []
        assert body["meta"]["private_scoring_available"] is False

    def test_fixture_data_is_labelled_as_not_live(self, client):
        body = client.get("/adherence/pt-fixture-9").json()
        assert body["meta"]["live_pharmacy_data"] is False
        assert body["data"]["score"]["patient_id"] == "pt-fixture-9"

    def test_lapsed_patient_gets_a_physician_alert(self, client):
        # The fixture driver returns a lapsed denosumab profile for ids not
        # ending 0 to 8.
        body = client.get("/adherence/pt-fixture-9").json()
        channels = {i["channel"] for i in body["data"]["interventions"]}
        assert "physician_alert" in channels

    def test_invalid_period_is_rejected_in_envelope_shape(self, client):
        response = client.post(
            "/adherence",
            json={
                "patient_id": "pt-bad",
                "fills": [],
                "period_start": TODAY.isoformat(),
                "period_end": PERIOD_START.isoformat(),
            },
        )
        assert response.status_code == 422
        body = response.json()
        assert set(body) == {"data", "error", "meta"}
        assert body["error"]["code"] == "validation_error"
