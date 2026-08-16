"""Unified internal schema: one T-score scale across every manufacturer.

THE PROBLEM
-----------
Hologic and GE Lunar measure the same femoral neck and report different
numbers. Two separate effects stack on top of each other.

1. The densitometers disagree on BMD. Different X-ray energies, different edge
   detection, different bone maps. Lunar reads systematically higher at the
   spine than Hologic on the same patient, by roughly ten percent.

2. They compute T-scores against different young adult reference databases.
   So even after the BMD disagreement is corrected, the same bone density
   converts to a different T-score depending on whose console printed it.

For a single scan neither effect matters much, because the physician reads the
number the vendor printed and treats accordingly. For a trajectory they are
fatal. A patient scanned on a Hologic in 2016 and a Lunar in 2022 shows a step
change between those two points that is entirely an artefact of the hardware.
Fit a line through it and the velocity is wrong, which means the projected
crossing date is wrong, which means the alert fires at the wrong time. The
entire product rests on that line being real.

THE CORRECTION
--------------
Both problems are solved in one pass:

    vendor BMD  ->  standardised BMD (sBMD)  ->  T-score vs one reference

The sBMD conversions come from the International Committee for Standards in
Bone Measurement cross-calibration studies (Genant 1994 for spine and total
hip, Lu 2001 for femoral neck), which scanned the same patients on multiple
manufacturers' machines and published the regressions. sBMD is expressed in
mg/cm2 by convention, which also makes it visually impossible to confuse a
standardised value with a raw vendor one.

The T-score is then recomputed from sBMD against a single NHANES III derived
young adult reference. After this, a Hologic point and a Lunar point sit on
the same axis and the slope between them means something.

WHAT IT DOES NOT FIX
--------------------
Cross-calibration regressions carry residual error of roughly 2 to 3 percent,
which is about 0.2 T-score units at the femoral neck. That is smaller than the
step it removes but not zero, so a mixed-vendor series is still flagged all the
way through to the chart. Before clinical deployment these coefficients should
be verified against a cross-calibration phantom on the actual scanners in use.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date

from app.engine import ge_lunar_adapter, hologic_adapter
from app.engine.pdf_parser import ParsedDocument
from app.schemas import (
    ExtractionSource,
    Manufacturer,
    NormalizedMeasurement,
    NormalizedScan,
    RawExtraction,
    SiteMeasurement,
    SkeletalSite,
)

logger = logging.getLogger("boensity.ingest.schema_mapper")

# Manufacturer detection below this score is not a confident call, and mapping
# a report with the wrong adapter produces plausible looking wrong numbers,
# which is the worst failure mode available.
MIN_DETECTION_SCORE = 0.25

# Below this a scan cannot be written to the timeline without a human looking.
REVIEW_CONFIDENCE_FLOOR = 0.80


@dataclass(frozen=True)
class Standardization:
    """sBMD (mg/cm2) = 1000 * (slope * vendor_bmd + intercept)."""

    slope: float
    intercept: float
    citation: str


# Published cross-calibration regressions. Norland coefficients are included
# because a minority of European clinics still run them and the day a Norland
# report arrives it should convert rather than fail.
_STANDARDIZATION: dict[tuple[SkeletalSite, Manufacturer], Standardization] = {
    (SkeletalSite.LUMBAR_SPINE, Manufacturer.HOLOGIC): Standardization(
        1.0755, 0.0, "Genant 1994, ICSBM spine cross-calibration"
    ),
    (SkeletalSite.LUMBAR_SPINE, Manufacturer.GE_LUNAR): Standardization(
        0.9522, 0.0, "Genant 1994, ICSBM spine cross-calibration"
    ),
    (SkeletalSite.TOTAL_HIP, Manufacturer.HOLOGIC): Standardization(
        1.008, 0.006, "Hanson 1997, ICSBM total hip cross-calibration"
    ),
    (SkeletalSite.TOTAL_HIP, Manufacturer.GE_LUNAR): Standardization(
        0.979, -0.031, "Hanson 1997, ICSBM total hip cross-calibration"
    ),
    (SkeletalSite.FEMORAL_NECK, Manufacturer.HOLOGIC): Standardization(
        1.013, 0.002, "Lu 2001, femoral neck cross-calibration"
    ),
    (SkeletalSite.FEMORAL_NECK, Manufacturer.GE_LUNAR): Standardization(
        0.939, 0.032, "Lu 2001, femoral neck cross-calibration"
    ),
}


@dataclass(frozen=True)
class YoungAdultReference:
    """Young adult mean and SD in sBMD units (mg/cm2).

    Derived by pushing the NHANES III young adult values through the Hologic
    standardisation above, so the reference lives on the same scale as every
    converted measurement. Sex-specific, per the ISCD position that men are
    scored against a male normative database.
    """

    mean_mg_cm2: float
    sd_mg_cm2: float


_YOUNG_ADULT_REFERENCE: dict[tuple[SkeletalSite, str], YoungAdultReference] = {
    (SkeletalSite.FEMORAL_NECK, "female"): YoungAdultReference(871.0, 121.6),
    (SkeletalSite.FEMORAL_NECK, "male"): YoungAdultReference(944.1, 133.7),
    (SkeletalSite.TOTAL_HIP, "female"): YoungAdultReference(955.6, 123.0),
    (SkeletalSite.TOTAL_HIP, "male"): YoungAdultReference(1031.1, 141.1),
    (SkeletalSite.LUMBAR_SPINE, "female"): YoungAdultReference(1126.0, 118.3),
    (SkeletalSite.LUMBAR_SPINE, "male"): YoungAdultReference(1170.1, 129.1),
}


def standardize_bmd(
    bmd_g_cm2: float, site: SkeletalSite, manufacturer: Manufacturer
) -> tuple[float | None, str | None]:
    """Convert a vendor BMD to sBMD in mg/cm2."""
    conversion = _STANDARDIZATION.get((site, manufacturer))
    if conversion is None:
        return None, None
    sbmd = 1000.0 * (conversion.slope * bmd_g_cm2 + conversion.intercept)
    return round(sbmd, 1), conversion.citation


def t_score_from_sbmd(sbmd_mg_cm2: float, site: SkeletalSite, sex: str) -> float | None:
    reference = _YOUNG_ADULT_REFERENCE.get((site, sex.lower()))
    if reference is None or reference.sd_mg_cm2 <= 0:
        return None
    return round((sbmd_mg_cm2 - reference.mean_mg_cm2) / reference.sd_mg_cm2, 2)


# ---------------------------------------------------------------------------
# Manufacturer detection
# ---------------------------------------------------------------------------


def detect_manufacturer(document: ParsedDocument) -> tuple[Manufacturer, float]:
    """Score both adapters and take the stronger claim.

    Running both and comparing beats short-circuiting on the first keyword
    hit, because service reports and multi-vendor comparison sheets can mention
    a competitor's name in passing.
    """
    scores = {
        Manufacturer.HOLOGIC: hologic_adapter.detect(document),
        Manufacturer.GE_LUNAR: ge_lunar_adapter.detect(document),
    }
    best = max(scores, key=lambda key: scores[key])
    if scores[best] < MIN_DETECTION_SCORE:
        return Manufacturer.UNKNOWN, scores[best]
    return best, scores[best]


def extract_with_best_adapter(
    document: ParsedDocument,
    source: ExtractionSource = ExtractionSource.PDF_TEXT_LAYER,
    ocr_confidence: float | None = None,
) -> RawExtraction:
    manufacturer, score = detect_manufacturer(document)

    if manufacturer is Manufacturer.HOLOGIC:
        return hologic_adapter.extract(document, source, ocr_confidence)
    if manufacturer is Manufacturer.GE_LUNAR:
        return ge_lunar_adapter.extract(document, source, ocr_confidence)

    # Unrecognised vendor. The stronger adapter still tends to find the table,
    # so it runs, but the result is labelled unknown and forced into review
    # rather than being silently attributed to a manufacturer.
    fallback = (
        hologic_adapter
        if hologic_adapter.detect(document) >= ge_lunar_adapter.detect(document)
        else ge_lunar_adapter
    )
    extraction = fallback.extract(document, source, ocr_confidence)
    extraction.manufacturer = Manufacturer.UNKNOWN
    extraction.detection_score = score
    extraction.warnings.append("manufacturer not identified, values cannot be standardised across scanners")
    return extraction


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def normalize_measurement(
    measurement: SiteMeasurement,
    manufacturer: Manufacturer,
    sex: str | None,
) -> NormalizedMeasurement | None:
    notes: list[str] = []
    confidences: list[float] = []

    bmd = None
    if measurement.bmd_g_cm2 is not None and measurement.bmd_g_cm2.value is not None:
        bmd = float(measurement.bmd_g_cm2.value)
        confidences.append(measurement.bmd_g_cm2.confidence)

    vendor_t = None
    if measurement.t_score is not None and measurement.t_score.value is not None:
        vendor_t = float(measurement.t_score.value)
        confidences.append(measurement.t_score.confidence)

    z_score = None
    if measurement.z_score is not None and measurement.z_score.value is not None:
        z_score = float(measurement.z_score.value)

    sbmd: float | None = None
    citation: str | None = None
    recomputed_t: float | None = None

    if bmd is not None and manufacturer is not Manufacturer.UNKNOWN:
        sbmd, citation = standardize_bmd(bmd, measurement.site, manufacturer)
        if sbmd is not None and sex:
            recomputed_t = t_score_from_sbmd(sbmd, measurement.site, sex)

    if recomputed_t is not None:
        t_score = recomputed_t
        is_recomputed = True
        notes.append(f"T-score recomputed from standardised BMD ({citation})")

        # A large gap between the vendor's number and ours usually means the
        # BMD or the site was misread, not that the standardisation is wrong.
        # Either way a physician needs to see it before it becomes a data point.
        if vendor_t is not None and abs(vendor_t - recomputed_t) > 0.5:
            notes.append(
                f"recomputed T-score {recomputed_t} differs from the printed {vendor_t} "
                "by more than 0.5, check the extracted BMD"
            )
    elif vendor_t is not None:
        t_score = vendor_t
        is_recomputed = False
        if bmd is None:
            notes.append("no BMD on the report, using the printed T-score without standardisation")
        elif manufacturer is Manufacturer.UNKNOWN:
            notes.append("manufacturer unknown, using the printed T-score without standardisation")
        elif not sex:
            notes.append("patient sex not supplied, using the printed T-score without standardisation")
    else:
        # Nothing usable at this site.
        return None

    return NormalizedMeasurement(
        site=measurement.site,
        bmd_g_cm2=bmd,
        standardized_bmd_mg_cm2=sbmd,
        vendor_t_score=vendor_t,
        t_score=t_score,
        z_score=z_score,
        t_score_is_recomputed=is_recomputed,
        confidence=round(min(confidences), 3) if confidences else 0.5,
        notes=notes,
    )


def normalize(
    extraction: RawExtraction,
    document_id: str | None = None,
    patient_id: str | None = None,
    sex: str | None = None,
    fallback_scan_date: date | None = None,
) -> NormalizedScan:
    """Turn a raw extraction into the unified schema the Timeline service stores."""
    document_id = document_id or uuid.uuid4().hex
    review_reasons: list[str] = list(extraction.warnings)

    scan_date: date | None = None
    if extraction.scan_date is not None and extraction.scan_date.value is not None:
        scan_date = extraction.scan_date.value
        if extraction.scan_date.confidence < REVIEW_CONFIDENCE_FLOOR:
            review_reasons.append("scan date confidence is low")

    if scan_date is None:
        # The date is the one field with no safe default. Without it a scan
        # cannot be placed on a trajectory at all, so it goes to review rather
        # than being assigned today's date and quietly corrupting the slope.
        scan_date = fallback_scan_date or date.today()
        review_reasons.append("scan date could not be read, defaulted and must be corrected")

    measurements: list[NormalizedMeasurement] = []
    for raw_measurement in extraction.measurements:
        normalized = normalize_measurement(raw_measurement, extraction.manufacturer, sex)
        if normalized is not None:
            measurements.append(normalized)

    if not measurements:
        review_reasons.append("no usable measurements after normalisation")

    if extraction.manufacturer is Manufacturer.UNKNOWN:
        review_reasons.append("manufacturer could not be identified")

    if sex is None and any(m.bmd_g_cm2 is not None for m in measurements):
        review_reasons.append(
            "patient sex not supplied, so BMD could not be converted to a standardised T-score"
        )

    measurement_confidence = min((m.confidence for m in measurements), default=0.0)
    date_confidence = extraction.scan_date.confidence if extraction.scan_date else 0.0
    overall = round(min(measurement_confidence, date_confidence) if measurements else 0.0, 3)

    requires_review = bool(review_reasons) or overall < REVIEW_CONFIDENCE_FLOOR

    return NormalizedScan(
        document_id=document_id,
        patient_id=patient_id,
        patient_reference=(str(extraction.patient_reference.value) if extraction.patient_reference else None),
        scan_date=scan_date,
        manufacturer=extraction.manufacturer,
        scanner_model=(str(extraction.scanner_model.value) if extraction.scanner_model else None),
        measurements=measurements,
        extraction_source=extraction.source,
        overall_confidence=overall,
        requires_review=requires_review,
        review_reasons=sorted(set(review_reasons)),
    )
