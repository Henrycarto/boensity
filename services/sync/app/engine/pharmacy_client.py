"""Pharmacy portal integration.

Pharmacy networks do not share one API. Some expose NCPDP SCRIPT, some expose
a REST fill history, and several still work by SFTP drop of a delimited file.
This module puts one interface in front of whatever a given network offers, so
the scoring engine never learns which kind of partner it is talking to.

Without live credentials the client runs a fixture driver against recorded
response shapes. That is what makes the rest of Sync developable and
demonstrable before a network agreement is signed, and it is honest about
being fixtures: fixture mode is reported through the API rather than being
disguised as live data.
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Any

import httpx

from app.schemas import DrugClass, FillRecord, ReferralCandidate

logger = logging.getLogger("boensity.sync.pharmacy")

DEFAULT_TIMEOUT = 15.0

# Mapping from the drug names that appear in pharmacy feeds to the classes
# adherence scoring understands. Feeds send brand names, generic names, and
# occasionally both, so matching is on a normalised substring.
_DRUG_CLASS_BY_NAME: dict[str, DrugClass] = {
    "alendronate": DrugClass.ORAL_BISPHOSPHONATE,
    "fosamax": DrugClass.ORAL_BISPHOSPHONATE,
    "risedronate": DrugClass.ORAL_BISPHOSPHONATE,
    "actonel": DrugClass.ORAL_BISPHOSPHONATE,
    "ibandronate": DrugClass.ORAL_BISPHOSPHONATE,
    "boniva": DrugClass.ORAL_BISPHOSPHONATE,
    "zoledronic": DrugClass.IV_BISPHOSPHONATE,
    "reclast": DrugClass.IV_BISPHOSPHONATE,
    "aclasta": DrugClass.IV_BISPHOSPHONATE,
    "denosumab": DrugClass.DENOSUMAB,
    "prolia": DrugClass.DENOSUMAB,
    "raloxifene": DrugClass.SERM,
    "evista": DrugClass.SERM,
    "teriparatide": DrugClass.ANABOLIC,
    "forteo": DrugClass.ANABOLIC,
    "abaloparatide": DrugClass.ANABOLIC,
    "romosozumab": DrugClass.ANABOLIC,
    "evenity": DrugClass.ANABOLIC,
    "calcium": DrugClass.SUPPLEMENT,
    "cholecalciferol": DrugClass.SUPPLEMENT,
    "vitamin d": DrugClass.SUPPLEMENT,
}


def classify_drug(drug_name: str) -> DrugClass:
    """Map a dispensed drug name to its class.

    Unrecognised names fall to oral bisphosphonate because it is the most
    common bone agent by a wide margin, and the misclassification it can cause
    is visible in the response rather than silent: the caller sees a drug name
    that does not match the class.
    """
    lowered = drug_name.lower()
    for token, drug_class in _DRUG_CLASS_BY_NAME.items():
        if token in lowered:
            return drug_class
    logger.info("unrecognised drug name %r, defaulting to oral bisphosphonate", drug_name)
    return DrugClass.ORAL_BISPHOSPHONATE


class PharmacyClientError(RuntimeError):
    pass


class PharmacyClient:
    """One interface over the connected pharmacy network."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        network_id: str | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("PHARMACY_API_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("PHARMACY_API_KEY", "")
        self.network_id = network_id or os.getenv("PHARMACY_NETWORK_ID", "")

    @property
    def live(self) -> bool:
        """True only with a real endpoint and credentials.

        Checked everywhere rather than assumed, so a missing credential
        produces fixtures that are labelled as fixtures instead of an
        exception in the middle of a demo.
        """
        return bool(self.base_url and self.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "X-Network-Id": self.network_id,
            "Accept": "application/json",
        }

    async def fetch_fills(self, patient_id: str, since: date | None = None) -> tuple[list[FillRecord], bool]:
        """Fill history for a patient. Returns the records and whether they are live."""
        if not self.live:
            return _fixture_fills(patient_id, since), False

        since = since or (date.today() - timedelta(days=365))

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.get(
                    f"{self.base_url}/patients/{patient_id}/fills",
                    params={"since": since.isoformat()},
                    headers=self._headers(),
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise PharmacyClientError(f"pharmacy portal request failed: {exc}") from exc

        return [_parse_fill(row) for row in payload.get("fills", [])], True

    async def fetch_referral_candidates(
        self, patient_id: str, drug_class: DrugClass
    ) -> tuple[list[ReferralCandidate], bool]:
        if not self.live:
            return _fixture_candidates(drug_class), False

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.get(
                    f"{self.base_url}/pharmacies",
                    params={"patient_id": patient_id, "drug_class": drug_class.value},
                    headers=self._headers(),
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise PharmacyClientError(f"pharmacy portal request failed: {exc}") from exc

        return [
            ReferralCandidate(
                pharmacy_id=str(row["id"]),
                pharmacy_name=row.get("name", "Unknown pharmacy"),
                distance_km=row.get("distance_km"),
                supports_drug_class=bool(row.get("supports_drug_class", False)),
                accepts_new_referrals=bool(row.get("accepting_referrals", False)),
                match_score=None,
                match_score_available=False,
            )
            for row in payload.get("pharmacies", [])
        ], True

    async def send_referral(
        self, patient_id: str, pharmacy_id: str, note: str
    ) -> tuple[dict[str, Any], bool]:
        if not self.live:
            return {"referral_id": f"fixture-{patient_id}-{pharmacy_id}", "status": "accepted"}, False

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.post(
                    f"{self.base_url}/referrals",
                    json={"patient_id": patient_id, "pharmacy_id": pharmacy_id, "note": note},
                    headers=self._headers(),
                )
                response.raise_for_status()
                return response.json(), True
        except httpx.HTTPError as exc:
            raise PharmacyClientError(f"referral submission failed: {exc}") from exc


def _parse_fill(row: dict[str, Any]) -> FillRecord:
    drug_name = row.get("drug_name") or row.get("description") or "unknown"
    return FillRecord(
        fill_date=date.fromisoformat(row["fill_date"]),
        days_supply=int(row.get("days_supply") or 30),
        drug_name=drug_name,
        drug_class=classify_drug(drug_name),
        quantity=row.get("quantity"),
        pharmacy_id=str(row["pharmacy_id"]) if row.get("pharmacy_id") else None,
        ndc=row.get("ndc"),
    )


# ---------------------------------------------------------------------------
# Fixture driver
# ---------------------------------------------------------------------------


def _fixture_fills(patient_id: str, since: date | None = None) -> list[FillRecord]:
    """Recorded response shapes for development without network credentials.

    Three deterministic profiles keyed off the patient id so the demo and the
    tests always show the same patient the same way. Deliberately not random:
    a demo where the adherence panel changes on every reload is not a demo.
    """
    today = date.today()
    tail = patient_id[-1].lower() if patient_id else "0"

    if tail in "012":
        # Adherent: monthly refills, no gaps.
        return [
            FillRecord(
                fill_date=today - timedelta(days=30 * index),
                days_supply=30,
                drug_name="Alendronate 70mg",
                drug_class=DrugClass.ORAL_BISPHOSPHONATE,
                pharmacy_id="ph-100",
            )
            for index in range(12, 0, -1)
        ]

    if tail in "345678":
        # Partial: started well, then a long gap, then resumed.
        offsets = [360, 330, 300, 270, 90, 60, 30]
        return [
            FillRecord(
                fill_date=today - timedelta(days=offset),
                days_supply=30,
                drug_name="Risedronate 35mg",
                drug_class=DrugClass.ORAL_BISPHOSPHONATE,
                pharmacy_id="ph-100",
            )
            for offset in offsets
        ]

    # Lapsed on a specialty injectable, the case that should route.
    return [
        FillRecord(
            fill_date=today - timedelta(days=offset),
            days_supply=1,
            drug_name="Prolia (denosumab) 60mg",
            drug_class=DrugClass.DENOSUMAB,
            pharmacy_id="ph-100",
        )
        for offset in (540, 360)
    ]


def _fixture_candidates(drug_class: DrugClass) -> list[ReferralCandidate]:
    specialty = drug_class in {DrugClass.DENOSUMAB, DrugClass.IV_BISPHOSPHONATE, DrugClass.ANABOLIC}
    return [
        ReferralCandidate(
            pharmacy_id="ph-201",
            pharmacy_name="Meridian Specialty Pharmacy",
            distance_km=3.4,
            supports_drug_class=True,
            accepts_new_referrals=True,
            match_score=None,
            match_score_available=False,
        ),
        ReferralCandidate(
            pharmacy_id="ph-202",
            pharmacy_name="Northgate Community Chemist",
            distance_km=1.1,
            supports_drug_class=not specialty,
            accepts_new_referrals=True,
            match_score=None,
            match_score_available=False,
        ),
        ReferralCandidate(
            pharmacy_id="ph-203",
            pharmacy_name="Coast Infusion Services",
            distance_km=11.8,
            supports_drug_class=True,
            accepts_new_referrals=False,
            match_score=None,
            match_score_available=False,
        ),
    ]


_default_client: PharmacyClient | None = None


def get_pharmacy_client() -> PharmacyClient:
    global _default_client
    if _default_client is None:
        _default_client = PharmacyClient()
    return _default_client
