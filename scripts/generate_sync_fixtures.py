"""Generate adherence demo fixtures from the real Sync engine.

Same principle as generate_demo_fixtures.py: the adherence panel in the demo
shows what the shipped scoring code produces, not numbers typed into a mock.

Fill dates are written relative to a fixed anchor and the scoring runs against
that same anchor, so the fixture is deterministic. A demo whose adherence band
changes depending on when the page is loaded is not a demo.

Run from the repository root:
    services/sync/.venv/Scripts/python scripts/generate_sync_fixtures.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "sync"))

from app.engine import adherence_scorer, referral_router
from app.engine.pharmacy_client import _fixture_candidates
from app.routers.adherence import build_interventions
from app.schemas import DrugClass, FillRecord

OUTPUT = ROOT / "apps" / "web" / "lib" / "demo-adherence.json"

# Fixed anchor so the fixture never drifts.
ANCHOR = date(2026, 3, 1)
PERIOD_START = ANCHOR - timedelta(days=364)


def fills(offsets: list[int], drug_class: DrugClass, name: str, days_supply: int = 30):
    return [
        FillRecord(
            fill_date=ANCHOR - timedelta(days=offset),
            days_supply=days_supply,
            drug_name=name,
            drug_class=drug_class,
            pharmacy_id="ph-100",
        )
        for offset in sorted(offsets, reverse=True)
    ]


# One profile per demo patient, matched to their clinical story.
PROFILES: dict[str, list[FillRecord]] = {
    # Not yet on treatment. No fills is a real and important state.
    "BN-4471": [],
    # Osteoporotic, on an oral agent, refilling erratically.
    "BN-2298": fills(
        [355, 325, 295, 265, 150, 120, 90, 60, 30],
        DrugClass.ORAL_BISPHOSPHONATE,
        "Alendronate 70mg",
    ),
    # Supplements only.
    "BN-8810": fills(
        [30 * index for index in range(12, 0, -1)],
        DrugClass.SUPPLEMENT,
        "Cholecalciferol 1000 IU",
    ),
    # Gaining density, and the fill record explains why.
    "BN-6034": fills(
        [30 * index for index in range(12, 0, -1)],
        DrugClass.ORAL_BISPHOSPHONATE,
        "Risedronate 35mg",
    ),
    # Lapsed on a six monthly injectable. The case that should route.
    "BN-1157": fills([400, 220], DrugClass.DENOSUMAB, "Prolia (denosumab) 60mg", days_supply=1),
}


def main() -> None:
    payload: dict[str, dict] = {}

    for patient_id, records in PROFILES.items():
        score = adherence_scorer.score_adherence(
            patient_id=patient_id,
            fills=records,
            period_start=PERIOD_START,
            period_end=ANCHOR,
            today=ANCHOR,
        )
        candidates = _fixture_candidates(score.drug_class)
        decision = referral_router.route(score, candidates)

        payload[patient_id] = {
            "score": json.loads(score.model_dump_json()),
            "interventions": [json.loads(item.model_dump_json()) for item in build_interventions(score)],
            "referral": json.loads(decision.model_dump_json()),
        }

        print(
            f"  {patient_id}: {score.band.value:13s} "
            f"PDC {score.proportion_days_covered * 100:5.1f}%  "
            f"gaps {len(score.gaps)}  referral {decision.status.value}"
        )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
