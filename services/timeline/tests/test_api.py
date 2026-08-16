"""Endpoint contract tests.

Every route must answer with {data, error, meta}, including on failure. The
frontend branches on that shape alone, so a route that returns a bare FastAPI
error detail would break the client even though the status code looks right.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


PATIENT = {
    "patient_id": "pt-7781",
    "age": 68.0,
    "sex": "female",
    "weight_kg": 58.0,
    "height_cm": 162.0,
    "previous_fracture": True,
    "parent_hip_fracture": False,
    "current_smoking": False,
    "glucocorticoids": False,
    "rheumatoid_arthritis": False,
    "secondary_osteoporosis": False,
    "alcohol_three_or_more_units": False,
}

DECLINING_SCANS = [
    {
        "scan_date": "2013-03-11",
        "t_score": -0.9,
        "bmd_g_cm2": 0.750,
        "site": "femoral_neck",
        "manufacturer": "hologic",
    },
    {
        "scan_date": "2016-05-02",
        "t_score": -1.3,
        "bmd_g_cm2": 0.702,
        "site": "femoral_neck",
        "manufacturer": "hologic",
    },
    {
        "scan_date": "2019-06-18",
        "t_score": -1.7,
        "bmd_g_cm2": 0.654,
        "site": "femoral_neck",
        "manufacturer": "hologic",
    },
    {
        "scan_date": "2023-09-07",
        "t_score": -2.2,
        "bmd_g_cm2": 0.594,
        "site": "femoral_neck",
        "manufacturer": "hologic",
    },
]


def assert_envelope(body: dict) -> None:
    assert set(body) == {"data", "error", "meta"}
    assert "service" in body["meta"]


class TestHealth:
    def test_health_returns_envelope(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert_envelope(response.json())
        assert response.json()["data"]["status"] == "ok"

    def test_root_lists_endpoints(self, client):
        body = client.get("/").json()
        assert_envelope(body)
        assert "/trajectory" in body["data"]["endpoints"]


class TestTrajectoryEndpoint:
    def test_declining_patient_returns_full_picture(self, client):
        response = client.post(
            "/trajectory",
            json={
                "patient": PATIENT,
                "scans": DECLINING_SCANS,
                "site": "femoral_neck",
                "projection_years": 10,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert_envelope(body)

        data = body["data"]
        assert data["patient_id"] == "pt-7781"
        assert data["scan_count"] == 4
        assert data["current_t_score"] == -2.2
        assert data["current_band"] == "osteopenia"
        assert data["velocity"]["direction"] == "declining"
        assert data["velocity"]["t_score_per_year"] < 0
        assert data["observation_span_years"] > 10

    def test_projection_crosses_the_osteoporosis_threshold(self, client):
        body = client.post(
            "/trajectory",
            json={
                "patient": PATIENT,
                "scans": DECLINING_SCANS,
                "site": "femoral_neck",
                "projection_years": 10,
            },
        ).json()

        crossings = {c["band_entered"]: c for c in body["data"]["crossings"]}
        osteoporosis = crossings["osteoporosis"]
        assert not osteoporosis["already_crossed"]
        assert osteoporosis["crossing_date"] is not None
        assert 0 < osteoporosis["years_until_crossing"] <= 10

        codes = {a["code"] for a in body["data"]["alerts"]}
        assert "projected_osteoporosis_crossing" in codes

    def test_points_split_into_history_and_projection(self, client):
        body = client.post(
            "/trajectory",
            json={
                "patient": PATIENT,
                "scans": DECLINING_SCANS,
                "site": "femoral_neck",
                "projection_years": 10,
            },
        ).json()

        points = body["data"]["points"]
        historical = [p for p in points if not p["is_projected"]]
        projected = [p for p in points if p["is_projected"]]
        assert len(historical) == 4
        assert len(projected) == 20
        assert all(p["ci_lower"] is not None for p in projected)

    def test_meta_carries_the_least_significant_change(self, client):
        body = client.post(
            "/trajectory",
            json={"patient": PATIENT, "scans": DECLINING_SCANS, "site": "femoral_neck"},
        ).json()
        assert body["meta"]["least_significant_change"] > 0

    def test_single_scan_is_accepted_and_labelled(self, client):
        body = client.post(
            "/trajectory",
            json={"patient": PATIENT, "scans": DECLINING_SCANS[:1], "site": "femoral_neck"},
        ).json()
        assert body["data"]["model_quality"] == "population_prior_single_scan"
        assert "single_scan_only" in {a["code"] for a in body["data"]["alerts"]}

    def test_site_with_no_scans_is_rejected_in_envelope_shape(self, client):
        response = client.post(
            "/trajectory",
            json={"patient": PATIENT, "scans": DECLINING_SCANS, "site": "lumbar_spine"},
        )
        assert response.status_code == 422
        body = response.json()
        assert_envelope(body)
        assert body["data"] is None
        assert body["error"]["code"] == "validation_error"

    def test_duplicate_scan_dates_are_rejected(self, client):
        response = client.post(
            "/trajectory",
            json={
                "patient": PATIENT,
                "scans": [DECLINING_SCANS[0], DECLINING_SCANS[0]],
                "site": "femoral_neck",
            },
        )
        assert response.status_code == 422
        assert "duplicate" in response.json()["error"]["message"].lower()

    def test_velocity_only_route_skips_the_point_series(self, client):
        body = client.post(
            "/trajectory/velocity",
            json={"patient": PATIENT, "scans": DECLINING_SCANS, "site": "femoral_neck"},
        ).json()
        assert_envelope(body)
        assert "points" not in body["data"]
        assert body["data"]["velocity"]["direction"] == "declining"


class TestFraxEndpoint:
    def test_returns_probability_and_treatment_delta(self, client):
        response = client.post(
            "/frax",
            json={"patient": PATIENT, "femoral_neck_t_score": -2.2, "include_treatment_delta": True},
        )
        assert response.status_code == 200
        data = response.json()["data"]

        assert data["major_osteoporotic_percent"] > 0
        assert data["hip_percent"] > 0
        assert data["bmd_included"] is True
        assert data["treatment"]["major_osteoporotic_percent"] < data["major_osteoporotic_percent"]
        assert data["treatment"]["absolute_risk_reduction_major"] > 0
        assert data["calibration_note"]

    def test_threshold_basis_is_always_explained(self, client):
        data = client.post("/frax", json={"patient": PATIENT, "femoral_neck_t_score": -2.2}).json()["data"]
        assert isinstance(data["intervention_threshold_met"], bool)
        assert len(data["threshold_basis"]) > 20

    def test_works_without_bmd(self, client):
        data = client.post("/frax", json={"patient": PATIENT}).json()["data"]
        assert data["bmd_included"] is False
        assert data["major_osteoporotic_percent"] > 0

    def test_age_outside_frax_validation_range_is_rejected(self, client):
        response = client.post("/frax", json={"patient": {**PATIENT, "age": 25.0}})
        assert response.status_code == 422
        assert_envelope(response.json())

    def test_projected_frax_rises_over_time(self, client):
        response = client.post(
            "/frax/projected",
            json={"patient": PATIENT, "scans": DECLINING_SCANS, "projection_years": 10, "step_years": 2},
        )
        assert response.status_code == 200
        points = response.json()["data"]

        assert len(points) == 6
        assert points[0]["year_offset"] == 0
        # Ageing and continued bone loss both push the number up, which is the
        # entire argument for treating now rather than at the next scan.
        assert points[-1]["major_osteoporotic_percent"] > points[0]["major_osteoporotic_percent"]
        assert points[-1]["projected_t_score"] < points[0]["projected_t_score"]

    def test_projected_frax_stops_at_age_90(self, client):
        elderly = {**PATIENT, "age": 86.0}
        points = client.post(
            "/frax/projected",
            json={"patient": elderly, "scans": DECLINING_SCANS, "projection_years": 10, "step_years": 2},
        ).json()["data"]
        assert all(p["age"] <= 90.0 for p in points)

    def test_projected_frax_requires_a_femoral_neck_scan(self, client):
        spine_only = [{**s, "site": "lumbar_spine"} for s in DECLINING_SCANS]
        response = client.post(
            "/frax/projected",
            json={"patient": PATIENT, "scans": spine_only},
        )
        assert response.status_code == 422
        body = response.json()
        assert_envelope(body)
        assert "femoral neck" in body["error"]["message"]
