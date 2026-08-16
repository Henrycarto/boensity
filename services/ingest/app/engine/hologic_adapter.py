"""Hologic report normaliser.

Covers the QDR, Discovery, Horizon, and APEX console output. The layout has
been stable across those generations: a header block with patient and scan
identifiers, then one results table per scanned region set.

The hip table looks like this, with the column order fixed:

    Region     Area(cm2)   BMC(g)   BMD(g/cm2)   T-score   PR(%)   Z-score   AM(%)
    Neck            4.71     2.94        0.624      -2.1      74      -0.6      92
    Troch           9.88     5.31        0.537      -1.5      80      -0.3      95
    Inter          19.44    15.02        0.773      -1.3      84      -0.4      94
    Total          34.03    23.27        0.684      -1.7      79      -0.5      93

Parsing runs off column position rather than a single regex over the whole
line. Hologic prints an optional Ward's row and some sites omit BMC, so a
regex pinned to a fixed number of fields breaks on perfectly valid reports.
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

logger = logging.getLogger("boensity.ingest.hologic")

FINGERPRINT: dict[str, float] = {
    "hologic": 3.0,
    "qdr": 2.0,
    "discovery": 1.5,
    "horizon": 1.5,
    "apex": 1.0,
    "pr(%)": 1.5,
    "am(%)": 1.5,
    "bmc": 0.5,
    "inter": 0.5,
}

# Hologic writes the intertrochanteric region as "Inter" and the composite as
# "Total". Ward's triangle is reported but is not used clinically for
# diagnosis, so it is read and then dropped rather than mapped to a site.
_SITE_LABELS: dict[str, SkeletalSite] = {
    "neck": SkeletalSite.FEMORAL_NECK,
    "femoral neck": SkeletalSite.FEMORAL_NECK,
    "total hip": SkeletalSite.TOTAL_HIP,
    "total": SkeletalSite.TOTAL_HIP,
    "l1-l4": SkeletalSite.LUMBAR_SPINE,
    "l2-l4": SkeletalSite.LUMBAR_SPINE,
    "l1_l4": SkeletalSite.LUMBAR_SPINE,
    "radius": SkeletalSite.FOREARM,
    "ultradistal": SkeletalSite.FOREARM,
    "33%": SkeletalSite.FOREARM,
}

_IGNORED_REGIONS = ("ward", "troch", "shaft", "mean", "l1 ", "l2 ", "l3 ", "l4 ")

_SCANNER_MODEL = re.compile(
    r"\b(QDR\s*\d{3,4}[A-Za-z]*|Discovery\s*[A-CWSQ]?|Horizon\s*[AWC]?|APEX)\b",
    re.IGNORECASE,
)

_DATE_LABELS = ("scan date", "measured", "analyzed", "analysed", "exam date", "date of scan")
_PATIENT_LABELS = ("patient id", "patient:", "id:", "name:", "mrn")


def detect(document: ParsedDocument) -> float:
    return score_fingerprint(document.full_text, FINGERPRINT)


def _spine_total_is_lumbar(line_text: str, in_spine_table: bool) -> bool:
    """ "Total" means the hip composite on a hip table and L1-L4 on a spine table.

    The word alone is ambiguous, so the table it sits under decides. Getting
    this backwards silently files a spine value as a hip value, and the two
    have different diagnostic thresholds.
    """
    return in_spine_table and "total" in line_text.lower()


def _is_spine_table_header(text: str) -> bool:
    lowered = text.lower()
    return ("l1" in lowered and "l4" in lowered) or "spine" in lowered or "ap lumbar" in lowered


def _is_hip_table_header(text: str) -> bool:
    lowered = text.lower()
    return "hip" in lowered or "femur" in lowered or ("neck" in lowered and "troch" in lowered)


def _extract_measurement(
    line: TextLine,
    site: SkeletalSite,
    source: ExtractionSource,
    confidence: float,
) -> SiteMeasurement | None:
    """Pull BMD, T-score, and Z-score off one table row.

    Hologic's numeric columns run Area, BMC, BMD, T-score, PR%, Z-score, AM%.
    Rather than trusting that count, each value is identified by the range it
    has to fall in: BMD is a small positive decimal, T and Z scores are signed
    and small, and the percentages are large integers. That survives a missing
    BMC column, which older QDR firmware omits.
    """
    repair = source is ExtractionSource.CLOUD_VISION_OCR
    values = numeric_tokens(line.text, repair_ocr=repair)
    if len(values) < 2:
        return None

    bmd = next((v for v in values if in_range(v, BMD_MIN, BMD_MAX) and "." in f"{v}"), None)

    # The scores follow BMD in the row. Searching after its index avoids
    # picking up an area value that happens to fall in the T-score range.
    after_bmd = values[values.index(bmd) + 1 :] if bmd in values else values
    signed = [v for v in after_bmd if in_range(v, T_SCORE_MIN, T_SCORE_MAX) and abs(v) < 8.0]

    t_score = signed[0] if signed else None
    z_score = next(
        (v for v in signed[1:] if in_range(v, Z_SCORE_MIN, Z_SCORE_MAX)),
        None,
    )

    if bmd is None and t_score is None:
        return None

    return SiteMeasurement(
        site=site,
        region_label=line.text.split()[0] if line.text.split() else site.value,
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
            # A date found without a label could be the print date or the
            # patient's birth date, so it is knocked down enough to force review.
            scan_date_value = make_value(found, source, confidence * 0.6, raw, 1)
            warnings.append("scan date found without a label, confirm before saving")
        else:
            warnings.append("no scan date found")

    # ---- scanner model ----
    scanner_value = None
    model_match = _SCANNER_MODEL.search(document.full_text)
    if model_match:
        scanner_value = make_value(model_match.group(1).strip(), source, confidence, model_match.group(0), 1)

    # ---- patient reference ----
    patient_value = None
    for line in document.lines:
        lowered = line.text.lower()
        if any(label in lowered for label in _PATIENT_LABELS):
            cleaned = re.sub(r"(?i)(patient\s*id|patient|mrn|name|id)\s*[:#]?\s*", "", line.text).strip()
            if cleaned:
                patient_value = make_value(cleaned[:64], source, confidence * 0.9, line.text, line.page)
                break

    # ---- results tables ----
    in_spine_table = False
    seen: set[SkeletalSite] = set()

    for line in document.lines:
        text = line.text.strip()
        if not text:
            continue

        if _is_spine_table_header(text):
            in_spine_table = True
        elif _is_hip_table_header(text):
            in_spine_table = False

        lowered = text.lower()
        if any(token in lowered for token in _IGNORED_REGIONS):
            continue

        site = site_from_label(text, _SITE_LABELS)
        if site is None:
            continue

        if _spine_total_is_lumbar(text, in_spine_table):
            site = SkeletalSite.LUMBAR_SPINE

        # First occurrence wins. Hologic repeats the results table in the
        # trend section of a serial report, and those rows are historical
        # comparisons, not this scan.
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
        manufacturer=Manufacturer.HOLOGIC,
        scanner_model=scanner_value,
        scan_date=scan_date_value,
        patient_reference=patient_value,
        measurements=measurements,
        source=source,
        page_count=document.page_count,
        detection_score=detect(document),
        warnings=warnings,
    )
