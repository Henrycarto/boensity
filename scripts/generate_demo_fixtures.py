"""Generate the demo fixtures the frontend falls back to.

The landing page chart and the demo patient screens must render before anyone
has a database, a pharmacy contract, or a Cloud Vision key. They still have to
show real model output, because a screenshot built from invented numbers is
worthless the moment someone technical asks how it was produced.

So the fixtures are computed by calling the actual Timeline engine here and
serialising the result. Every T-score, velocity, crossing date, and FRAX
probability in the demo is what the shipped code produces for that input.

Run from the repository root:
    services/timeline/.venv/Scripts/python scripts/generate_demo_fixtures.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "timeline"))

from app.engine import frax_calculator, threshold_engine, trajectory_model
from app.schemas import (
    Manufacturer,
    PatientContext,
    ScanObservation,
    Sex,
    SkeletalSite,
)

OUTPUT = ROOT / "apps" / "web" / "lib" / "demo-data.json"

# Fixed anchor so the fixture never drifts. The stale scan alert measures the
# gap between the latest scan and "today"; left to the wall clock, CI failed
# on its own once a demo patient crossed the three year threshold. The date
# is the one the committed fixtures were last generated on, so anchoring
# changed nothing in the demo. generate_sync_fixtures.py anchors the same way.
ANCHOR = date(2026, 8, 18)


def scan(
    year: int,
    month: int,
    day: int,
    t_score: float,
    bmd: float,
    manufacturer: Manufacturer = Manufacturer.HOLOGIC,
    site: SkeletalSite = SkeletalSite.FEMORAL_NECK,
    model: str = "Discovery A",
) -> ScanObservation:
    return ScanObservation(
        scan_date=date(year, month, day),
        t_score=t_score,
        bmd_g_cm2=bmd,
        site=site,
        manufacturer=manufacturer,
        scanner_model=model,
    )


# ---------------------------------------------------------------------------
# Demo cohort
# ---------------------------------------------------------------------------
# Five patients chosen to cover the states the interface has to handle: a clear
# crossing, an already-osteoporotic patient, a stable patient inside the noise
# floor, a treatment responder, and a mixed-manufacturer series.

DEMO_PATIENTS = [
    {
        "id": "BN-4471",
        "name": "Patient BN-4471",
        "note": "Steady decline, projected to cross inside the window",
        "patient": PatientContext(
            patient_id="BN-4471",
            age=64.0,
            sex=Sex.FEMALE,
            weight_kg=58.0,
            height_cm=163.0,
            previous_fracture=False,
            parent_hip_fracture=True,
            current_smoking=False,
            glucocorticoids=False,
            rheumatoid_arthritis=False,
            secondary_osteoporosis=False,
            alcohol_three_or_more_units=False,
        ),
        "scans": [
            scan(2012, 4, 18, -0.6, 0.786),
            scan(2015, 6, 2, -1.0, 0.738),
            scan(2018, 5, 21, -1.4, 0.690),
            scan(2021, 9, 14, -1.8, 0.642),
            scan(2024, 10, 8, -2.1, 0.606),
        ],
    },
    {
        "id": "BN-2298",
        "name": "Patient BN-2298",
        "note": "Already osteoporotic, treatment decision today",
        "patient": PatientContext(
            patient_id="BN-2298",
            age=73.0,
            sex=Sex.FEMALE,
            weight_kg=51.0,
            height_cm=158.0,
            previous_fracture=True,
            parent_hip_fracture=False,
            current_smoking=True,
            glucocorticoids=False,
            rheumatoid_arthritis=False,
            secondary_osteoporosis=False,
            alcohol_three_or_more_units=False,
        ),
        "scans": [
            scan(2016, 2, 11, -1.9, 0.630),
            scan(2019, 3, 5, -2.3, 0.582),
            scan(2022, 4, 19, -2.6, 0.546),
            scan(2025, 3, 27, -2.9, 0.510),
        ],
    },
    {
        "id": "BN-8810",
        "name": "Patient BN-8810",
        "note": "Change inside the measurement noise floor",
        "patient": PatientContext(
            patient_id="BN-8810",
            age=59.0,
            sex=Sex.FEMALE,
            weight_kg=71.0,
            height_cm=167.0,
        ),
        "scans": [
            scan(2017, 8, 3, -1.5, 0.678),
            scan(2020, 9, 16, -1.52, 0.676),
            scan(2023, 8, 29, -1.55, 0.672),
        ],
    },
    {
        "id": "BN-6034",
        "name": "Patient BN-6034",
        "note": "Gaining density on treatment",
        "patient": PatientContext(
            patient_id="BN-6034",
            age=68.0,
            sex=Sex.FEMALE,
            weight_kg=63.0,
            height_cm=161.0,
            previous_fracture=True,
        ),
        "scans": [
            scan(2019, 1, 22, -2.8, 0.522),
            scan(2021, 2, 9, -2.6, 0.546),
            scan(2023, 3, 14, -2.4, 0.570),
            scan(2025, 2, 25, -2.2, 0.594),
        ],
    },
    {
        "id": "BN-1157",
        "name": "Patient BN-1157",
        "note": "Series spans a scanner replacement",
        "patient": PatientContext(
            patient_id="BN-1157",
            age=66.0,
            sex=Sex.FEMALE,
            weight_kg=55.0,
            height_cm=160.0,
            glucocorticoids=True,
        ),
        "scans": [
            scan(2014, 5, 6, -1.1, 0.726, Manufacturer.HOLOGIC),
            scan(2017, 7, 18, -1.5, 0.678, Manufacturer.HOLOGIC),
            scan(2020, 6, 30, -1.9, 0.700, Manufacturer.GE_LUNAR, model="Prodigy Advance"),
            scan(2023, 8, 11, -2.2, 0.664, Manufacturer.GE_LUNAR, model="Prodigy Advance"),
        ],
    },
]


def build_patient(entry: dict) -> dict:
    patient: PatientContext = entry["patient"]
    scans: list[ScanObservation] = entry["scans"]

    fit = trajectory_model.fit_trajectory(scans, SkeletalSite.FEMORAL_NECK, patient.sex)
    velocity = trajectory_model.build_velocity_report(fit, scans)
    points = trajectory_model.project_points(fit, scans, projection_years=10)
    crossings = threshold_engine.detect_all_crossings(fit, velocity, 10)

    latest = max(scans, key=lambda item: item.scan_date)
    frax = frax_calculator.calculate_frax(patient, latest.t_score)
    threshold_met, basis = frax_calculator.meets_intervention_threshold(frax)
    treatment = frax_calculator.apply_treatment(frax, "bisphosphonate")

    alerts = threshold_engine.build_alerts(
        fit=fit,
        velocity=velocity,
        crossings=crossings,
        scans=scans,
        current_t_score=latest.t_score,
        frax=frax,
        today=ANCHOR,
    )

    span = (
        max(item.scan_date for item in scans) - min(item.scan_date for item in scans)
    ).days / trajectory_model.DAYS_PER_YEAR

    return {
        "id": entry["id"],
        "name": entry["name"],
        "note": entry["note"],
        "patient": json.loads(patient.model_dump_json()),
        "scans": [json.loads(item.model_dump_json()) for item in scans],
        "trajectory": {
            "patient_id": patient.patient_id,
            "site": SkeletalSite.FEMORAL_NECK.value,
            "scan_count": len(scans),
            "observation_span_years": round(span, 2),
            "current_t_score": round(latest.t_score, 2),
            "current_band": trajectory_model.classify_band(latest.t_score).value,
            "points": [json.loads(point.model_dump_json()) for point in points],
            "velocity": json.loads(velocity.model_dump_json()),
            "crossings": [json.loads(crossing.model_dump_json()) for crossing in crossings],
            "alerts": [json.loads(alert.model_dump_json()) for alert in alerts],
            "model_quality": trajectory_model.describe_model_quality(fit, scans),
        },
        "frax": {
            "patient_id": patient.patient_id,
            "major_osteoporotic_percent": frax.major_osteoporotic_percent,
            "hip_percent": frax.hip_percent,
            "bmd_included": frax.bmd_included,
            "intervention_threshold_met": threshold_met,
            "threshold_basis": basis,
            "contributing_factors": frax.contributing_factors,
            "treatment": treatment,
            "calibration_note": frax_calculator.CALIBRATION_NOTE,
        },
    }


def main() -> None:
    payload = {
        "generated_by": "scripts/generate_demo_fixtures.py",
        "note": (
            "Computed by the Timeline engine, not hand written. Regenerate after any "
            "change to trajectory_model, frax_calculator, or threshold_engine."
        ),
        "patients": [build_patient(entry) for entry in DEMO_PATIENTS],
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    for patient in payload["patients"]:
        trajectory = patient["trajectory"]
        crossing = next(
            (c for c in trajectory["crossings"] if c["band_entered"] == "osteoporosis"),
            None,
        )
        when = (
            "already crossed"
            if crossing and crossing["already_crossed"]
            else (
                f"crosses in {crossing['years_until_crossing']}y"
                if crossing and crossing["crossing_date"]
                else "no crossing in window"
            )
        )
        print(
            f"  {patient['id']}: T {trajectory['current_t_score']:>5} "
            f"{trajectory['velocity']['t_score_per_year']:+.3f}/yr  "
            f"FRAX {patient['frax']['major_osteoporotic_percent']:>5.1f}%  {when}"
        )


if __name__ == "__main__":
    main()
