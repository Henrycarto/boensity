"""Shared parsing primitives for the manufacturer adapters.

Everything here is vendor-neutral: reading a date, reading a signed decimal,
deciding whether a number is physically possible. The vendor-specific parts
(which words label which skeletal site, where the columns sit, what the header
looks like) live in the individual adapters, because that is the only part
that actually differs and duplicating the rest would mean fixing every date
format bug twice.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from app.schemas import ExtractedValue, ExtractionSource, SkeletalSite

# DEXA reports print dates in whatever the console locale was set to, and a
# clinic's archive spans a decade of consoles. Ordered most to least specific
# so an unambiguous four digit year wins before a two digit guess.
_DATE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(\d{4}-\d{2}-\d{2})\b", "%Y-%m-%d"),
    (r"\b(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\b", "%d %B %Y"),
    (r"\b([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})\b", "%B %d, %Y"),
    (r"\b(\d{1,2}\.\d{1,2}\.\d{4})\b", "%d.%m.%Y"),
    (r"\b(\d{1,2}/\d{1,2}/\d{4})\b", "%m/%d/%Y"),
    (r"\b(\d{1,2}-\d{1,2}-\d{4})\b", "%d-%m-%Y"),
)

_MONTH_ABBREVIATED = "%d %b %Y"
_MONTH_ABBREVIATED_COMMA = "%b %d, %Y"

# Physical plausibility bounds. A BMD of 12.0 is an OCR misread of 1.20 with a
# stray decimal, not a patient. Rejecting it here is cheaper than explaining a
# nonsense chart later.
BMD_MIN, BMD_MAX = 0.100, 2.500
T_SCORE_MIN, T_SCORE_MAX = -8.0, 6.0
Z_SCORE_MIN, Z_SCORE_MAX = -8.0, 6.0

# OCR routinely confuses these inside numeric fields. Applied only to strings
# already known to be in a numeric column, never to free text.
_OCR_DIGIT_FIXES = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "S": "5", "B": "8"})

# A minus sign that came through as a hyphen variant, an en dash, or a Unicode
# minus. Getting this wrong flips a T-score of -2.5 into +2.5, which is the
# difference between osteoporosis and exceptional bone.
# The lint suppression below is deliberate: the ambiguous Unicode characters in
# this class are exactly what the pattern exists to catch.
_MINUS_VARIANTS = re.compile(r"[‐-―−˗－]")  # noqa: RUF001


def normalise_minus(text: str) -> str:
    return _MINUS_VARIANTS.sub("-", text)


def parse_date(text: str) -> tuple[date, str] | None:
    """First plausible date in the text, with the literal string it matched.

    Returns None rather than guessing. A wrong scan date silently reorders a
    patient's trajectory and inverts the slope, so an unparsed date must go to
    human review instead of becoming a default.
    """
    cleaned = normalise_minus(text)

    for pattern, fmt in _DATE_PATTERNS:
        for match in re.finditer(pattern, cleaned):
            raw = match.group(1)
            for candidate_fmt in _format_variants(fmt):
                try:
                    parsed = datetime.strptime(raw.replace(",", ""), candidate_fmt.replace(",", "")).date()
                except ValueError:
                    continue
                if _plausible_scan_date(parsed):
                    return parsed, raw
    return None


def _format_variants(fmt: str) -> tuple[str, ...]:
    if fmt == "%d %B %Y":
        return ("%d %B %Y", _MONTH_ABBREVIATED)
    if fmt == "%B %d, %Y":
        return ("%B %d, %Y", _MONTH_ABBREVIATED_COMMA)
    return (fmt,)


def _plausible_scan_date(value: date) -> bool:
    """Clinical DEXA started in the late 1980s and nothing is scanned tomorrow."""
    today = date.today()
    return date(1985, 1, 1) <= value <= today


def parse_number(token: str, repair_ocr: bool = False) -> float | None:
    """Read a signed decimal out of a table cell."""
    cleaned = normalise_minus(token).strip().strip("()")
    if repair_ocr:
        cleaned = cleaned.translate(_OCR_DIGIT_FIXES)

    # Vendor tables pad cells with units and percent signs.
    cleaned = re.sub(r"[^\d.\-+]", "", cleaned)
    if not cleaned or cleaned in {"-", "+", ".", "-.", "+."}:
        return None

    try:
        return float(cleaned)
    except ValueError:
        return None


def in_range(value: float | None, low: float, high: float) -> bool:
    return value is not None and low <= value <= high


def numeric_tokens(line_text: str, repair_ocr: bool = False) -> list[float]:
    """Every number on a line, in reading order.

    Vendor result tables are positional: the site label comes first, then a
    fixed sequence of numeric columns. Pulling the numbers in order and
    indexing into them survives column width changes between console versions,
    where a fixed character offset would not.
    """
    values: list[float] = []
    for token in normalise_minus(line_text).split():
        parsed = parse_number(token, repair_ocr=repair_ocr)
        if parsed is not None:
            values.append(parsed)
    return values


def make_value(
    value,
    source: ExtractionSource,
    confidence: float,
    raw_text: str | None = None,
    page: int | None = None,
) -> ExtractedValue:
    return ExtractedValue(
        value=value,
        confidence=max(0.0, min(1.0, confidence)),
        source=source,
        raw_text=raw_text,
        page=page,
    )


def base_confidence(source: ExtractionSource, ocr_confidence: float | None = None) -> float:
    """Starting confidence before field-level checks.

    A text layer is a lossless read of what the console wrote. OCR is a
    reconstruction. Starting them at the same number would let a blurry fax
    reach the timeline with the same authority as a native export.
    """
    if source is ExtractionSource.PDF_TEXT_LAYER:
        return 0.97
    if ocr_confidence is not None:
        return max(0.40, min(0.94, ocr_confidence))
    return 0.80


def site_from_label(label: str, mapping: dict[str, SkeletalSite]) -> SkeletalSite | None:
    """Longest label match wins.

    "Upper Neck" and "Neck" are different regions on a Lunar report, and a
    shortest-match lookup would fold the first into the second.
    """
    haystack = label.lower()
    best: tuple[int, SkeletalSite] | None = None
    for token, site in mapping.items():
        if token in haystack and (best is None or len(token) > best[0]):
            best = (len(token), site)
    return best[1] if best else None


def score_fingerprint(text: str, markers: dict[str, float]) -> float:
    """Weighted keyword score used for manufacturer detection.

    Weighted rather than counted because "Hologic" appearing once is decisive
    while "Neck" appearing once means nothing. Normalised to 0..1 so the two
    adapters' scores are directly comparable.
    """
    lowered = text.lower()
    hit = sum(weight for marker, weight in markers.items() if marker in lowered)
    total = sum(markers.values())
    return min(1.0, hit / total) if total else 0.0
