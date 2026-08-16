"""GE Lunar report normaliser.

Covers Prodigy, iDXA, and DPX consoles running enCORE software. The layout
differs from Hologic in three ways that matter to a parser.

First, the column set is narrower and pairs each score with a percentage:

    Region        BMD (g/cm2)   Young Adult (%)   T-score   Age Matched (%)   Z-score
    Neck                0.712               81       -1.6                94       -0.4
    Upper Neck          0.641               78       -1.9                91       -0.6
    Total               0.798               84       -1.3                96       -0.3

Second, Lunar reports a "Upper Neck" region that Hologic does not. It is not
the femoral neck and must not be filed as one.

Third, and the reason this is not a cosmetic difference: Lunar and Hologic
measure the same bone and report different BMD, and they compute T-scores
against different young adult reference databases. A patient whose scanner
changed between visits shows a step in their trend line that is an artefact of
the hardware, not their skeleton. Correcting that is schema_mapper's job; this
adapter's job is to report the vendor's own numbers faithfully and label them
as Lunar so the correction can be applied.
"""

from __future__ import annotations

import logging
import re

from app.engine.adapter_base import (
    BMD_MAX,
    BMD_MIN,
    T_SCORE_MAX,
    T_SCORE_MIN,
    Z_SCORE_MAX,
    Z_SCORE_MIN,
    base_confidence,
    in_range,
    make_value,
    numeric_tokens,
    parse_date,
    score_fingerprint,
    site_from_label,
)
from app.engine.pdf_parser import ParsedDocument, TextLine
from app.schemas import (
    ExtractionSource,
    Manufacturer,
    RawExtraction,
    SiteMeasurement,
    SkeletalSite,
)

logger = logging.getLogger("boensity.ingest.ge_lunar")

FINGERPRINT: dict[str, float] = {
    "lunar": 3.0,
    "ge healthcare": 2.5,
    "encore": 2.0,
    "prodigy": 1.5,
    "idxa": 1.5,
    "dpx": 1.0,
    "young adult": 1.5,
    "age matched": 1.5,
    "upper neck": 1.0,
}

# "upper neck" is listed so the longest-match lookup can claim it and return
# None, keeping it out of the femoral neck slot.
_SITE_LABELS: dict[str, SkeletalSite] = {
    "neck": SkeletalSite.FEMORAL_NECK,
    "femur neck": SkeletalSite.FEMORAL_NECK,
    "femoral neck": SkeletalSite.FEMORAL_NECK,
    "total": SkeletalSite.TOTAL_HIP,
    "total hip": SkeletalSite.TOTAL_HIP,
    "l1-l4": SkeletalSite.LUMBAR_SPINE,
    "l2-l4": SkeletalSite.LUMBAR_SPINE,
    "ap spine": SkeletalSite.LUMBAR_SPINE,
    "radius total": SkeletalSite.FOREARM,
    "radius 33%": SkeletalSite.FOREARM,
    "ultradistal": SkeletalSite.FOREARM,
}

# Regions Lunar prints that are not diagnostic sites. "Upper neck" leads the
# list because it contains "neck" and would otherwise be captured.
_IGNORED_REGIONS = ("upper neck", "ward", "troch", "shaft", "mean", "midline")

# Horizontal whitespace only. "GE Healthcare Lunar" and the model name sit on
# separate lines in the header, and \s would happily match the newline between
# them and return "Lunar\nProdigy" as the model.
_SCANNER_MODEL = re.compile(
    r"\b(Prodigy(?:[^\S\n]+(?:Advance|Pro))?|iDXA|DPX(?:[^\S\n]*(?:NT|MD|Bravo|Duo))?|Lunar[^\S\n]+\w+)\b",
    re.IGNORECASE,
)

_DATE_LABELS = ("measured", "scan date", "acquired", "exam date", "analysis date", "date:")
_PATIENT_LABELS = ("patient", "id:", "name:", "mrn", "birth")


def detect(document: ParsedDocument) -> float:
    return score_fingerprint(document.full_text, FINGERPRINT)


def _is_spine_context(text: str) -> bool:
    lowered = text.lower()
    return "spine" in lowered or ("l1" in lowered and "l4" in lowered) or "lumbar" in lowered


def _is_hip_context(text: str) -> bool:
    lowered = text.lower()
    return "femur" in lowered or "hip" in lowered or "dualfemur" in lowered


def _extract_measurement(
    line: TextLine,
    site: SkeletalSite,
    source: ExtractionSource,
    confidence: float,
) -> SiteMeasurement | None:
    """Read one Lunar table row.

    The percentage columns are the trap here. A row reads BMD, YA%, T-score,
    AM%, Z-score, so the percentages sit between the two scores. They are
    filtered by magnitude: a percentage of young adult mean runs from roughly
    50 to 130, well clear of the signed score range.
    """
    repair = source is ExtractionSource.CLOUD_VISION_OCR
    values = numeric_tokens(line.text, repair_ocr=repair)
    if len(values) < 2:
        return None

    bmd = next((v for v in values if in_range(v, BMD_MIN, BMD_MAX)), None)

    tail = values[values.index(bmd) + 1 :] if bmd is not None and bmd in values else values
    # Percentages are integers of large magnitude; scores are small and signed.
    scores = [v for v in tail if in_range(v, T_SCORE_MIN, T_SCORE_MAX) and abs(v) <= 8.0]

    t_score = scores[0] if scores else None
    z_score = next((v for v in scores[1:] if in_range(v, Z_SCORE_MIN, Z_SCORE_MAX)), None)

    if bmd is None and t_score is None:
        return None

    tokens = line.text.split()
    return SiteMeasurement(
        site=site,
        region_label=" ".join(tokens[:2]) if len(tokens) >= 2 else site.value,
        bmd_g_cm2=make_value(bmd, source, confidence, line.text, line.page) if bmd is not None else None,
        t_score=make_value(t_score, source, confidence, line.text, line.page)
        if t_score is not None
        else None,
        z_score=make_value(z_score, source, confidence * 0.95, line.text, line.page)
        if z_score is not None
        else None,
    )


def extract(
    document: ParsedDocument,
    source: ExtractionSource = ExtractionSource.PDF_TEXT_LAYER,
    ocr_confidence: float | None = None,
) -> RawExtraction:
    confidence = base_confidence(source, ocr_confidence)
    warnings: list[str] = []
    measurements: list[SiteMeasurement] = []

    # ---- scan date ----
    scan_date_value = None
    for line in document.lines:
        lowered = line.text.lower()
        if any(label in lowered for label in _DATE_LABELS):
            parsed = parse_date(line.text)
            if parsed:
                found, raw = parsed
                scan_date_value = make_value(found, source, confidence, raw, line.page)
                break

    if scan_date_value is None:
        parsed = parse_date(document.full_text)
        if parsed:
            found, raw = parsed
            scan_date_value = make_value(found, source, confidence * 0.6, raw, 1)
            warnings.append("scan date found without a label, confirm before saving")
        else:
            warnings.append("no scan date found")

    # European Lunar consoles print day first and US consoles print month
    # first. Both render as dd.mm.yyyy or mm/dd/yyyy respectively, and a date
    # where both parts are 12 or under is genuinely ambiguous from the text
    # alone. Flagging beats guessing, because a swapped month silently
    # reorders the patient's trajectory.
    if scan_date_value is not None and scan_date_value.raw_text:
        parts = re.split(r"[./-]", scan_date_value.raw_text)
        if (
            len(parts) == 3
            and parts[0].isdigit()
            and parts[1].isdigit()
            and int(parts[0]) <= 12
            and int(parts[1]) <= 12
            and parts[0] != parts[1]
        ):
            warnings.append(
                f"date {scan_date_value.raw_text} is ambiguous between day-first and "
                "month-first order, confirm before saving"
            )

    # ---- scanner model ----
    scanner_value = None
    model_match = _SCANNER_MODEL.search(document.full_text)
    if model_match:
        scanner_value = make_value(model_match.group(1).strip(), source, confidence, model_match.group(0), 1)

    # ---- patient reference ----
    patient_value = None
    for line in document.lines:
        lowered = line.text.lower()
        if any(label in lowered for label in _PATIENT_LABELS) and not _is_hip_context(lowered):
            cleaned = re.sub(
                r"(?i)(patient\s*id|patient|mrn|name|id|birth\s*date)\s*[:#]?\s*", "", line.text
            ).strip()
            if cleaned:
                patient_value = make_value(cleaned[:64], source, confidence * 0.9, line.text, line.page)
                break

    # ---- results tables ----
    in_spine = False
    seen: set[SkeletalSite] = set()

    for line in document.lines:
        text = line.text.strip()
        if not text:
            continue

        if _is_spine_context(text):
            in_spine = True
        elif _is_hip_context(text):
            in_spine = False

        lowered = text.lower()
        if any(token in lowered for token in _IGNORED_REGIONS):
            continue

        site = site_from_label(text, _SITE_LABELS)
        if site is None:
            continue

        if in_spine and site is SkeletalSite.TOTAL_HIP:
            site = SkeletalSite.LUMBAR_SPINE

        if site in seen:
            continue

        measurement = _extract_measurement(line, site, source, confidence)
        if measurement is not None:
            measurements.append(measurement)
            seen.add(site)

    if not measurements:
        warnings.append("no site measurements recognised in this report")

    if SkeletalSite.FEMORAL_NECK not in seen:
        warnings.append("no femoral neck measurement, FRAX cannot use BMD for this scan")

    return RawExtraction(
        manufacturer=Manufacturer.GE_LUNAR,
        scanner_model=scanner_value,
        scan_date=scan_date_value,
        patient_reference=patient_value,
        measurements=measurements,
        source=source,
        page_count=document.page_count,
        detection_score=detect(document),
        warnings=warnings,
    )
