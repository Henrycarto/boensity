"""pdfplumber reader for text-native PDFs.

Tried before Cloud Vision, not after. A large share of DEXA reports arrive as
PDFs exported directly from the scanner console, and those carry a real text
layer. Reading it is exact, free, and takes milliseconds, where OCR is
approximate, billed per page, and takes seconds. Sending a text-native PDF to
OCR is the most common way an ingest pipeline burns money for worse results.

The output keeps word positions because vendor reports are tables, and a table
flattened into a single string loses the row and column relationships that say
which T-score belongs to which skeletal site.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import pdfplumber

logger = logging.getLogger("boensity.ingest.pdf_parser")

# A text layer holding only a handful of characters usually means the PDF is a
# scanned image with a thin layer of junk from the scanning software. Below
# this per page threshold the document goes to OCR instead.
MIN_CHARS_PER_PAGE_FOR_TEXT_LAYER = 120


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    page: int

    @property
    def centre_y(self) -> float:
        return (self.top + self.bottom) / 2.0


@dataclass
class TextLine:
    """Words sharing a horizontal band, left to right."""

    words: list[Word]
    page: int

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)

    @property
    def top(self) -> float:
        return min(word.top for word in self.words)


@dataclass
class ParsedDocument:
    full_text: str
    lines: list[TextLine] = field(default_factory=list)
    page_count: int = 0
    has_usable_text_layer: bool = True

    def find_lines(self, pattern: str, flags: int = re.IGNORECASE) -> list[TextLine]:
        compiled = re.compile(pattern, flags)
        return [line for line in self.lines if compiled.search(line.text)]


def _group_words_into_lines(words: list[Word], tolerance: float = 3.0) -> list[TextLine]:
    """Cluster words by vertical position.

    Vendor reports do not emit words in reading order, and a fixed row height
    fails because font sizes vary between the header block and the results
    table. Clustering on the vertical midpoint with a small tolerance handles
    both without per-vendor tuning.
    """
    if not words:
        return []

    lines: list[TextLine] = []
    for page in sorted({word.page for word in words}):
        page_words = sorted(
            (word for word in words if word.page == page),
            key=lambda word: (word.centre_y, word.x0),
        )

        current: list[Word] = [page_words[0]]
        for word in page_words[1:]:
            if abs(word.centre_y - current[-1].centre_y) <= tolerance:
                current.append(word)
            else:
                lines.append(TextLine(sorted(current, key=lambda w: w.x0), page))
                current = [word]
        lines.append(TextLine(sorted(current, key=lambda w: w.x0), page))

    return lines


def parse_pdf(pdf_bytes: bytes) -> ParsedDocument:
    """Read a PDF's embedded text layer.

    Raises nothing on a scanned document. It returns has_usable_text_layer
    False so the caller can escalate to OCR, because "this PDF is an image" is
    an expected input, not an error condition.
    """
    import io

    words: list[Word] = []
    text_chunks: list[str] = []
    page_count = 0

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page_count = len(pdf.pages)
        for index, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text() or ""
            text_chunks.append(page_text)

            for raw in page.extract_words(use_text_flow=False, keep_blank_chars=False):
                words.append(
                    Word(
                        text=raw["text"],
                        x0=float(raw["x0"]),
                        x1=float(raw["x1"]),
                        top=float(raw["top"]),
                        bottom=float(raw["bottom"]),
                        page=index,
                    )
                )

    full_text = "\n".join(text_chunks)
    usable = page_count > 0 and (len(full_text.strip()) / page_count) >= MIN_CHARS_PER_PAGE_FOR_TEXT_LAYER

    if not usable:
        logger.info(
            "text layer too thin (%d chars across %d pages), OCR required",
            len(full_text.strip()),
            page_count,
        )

    return ParsedDocument(
        full_text=full_text,
        lines=_group_words_into_lines(words),
        page_count=page_count,
        has_usable_text_layer=usable,
    )


def parsed_from_ocr_text(text: str, page_count: int = 1) -> ParsedDocument:
    """Wrap flat OCR output in the same structure the adapters consume.

    Positions are lost, so line grouping falls back to the newlines Cloud
    Vision already inferred. Adapters that rely on column geometry degrade to
    their regex path, which is why each one keeps both.
    """
    lines: list[TextLine] = []
    for index, raw_line in enumerate(text.splitlines()):
        stripped = raw_line.strip()
        if not stripped:
            continue
        words = [
            Word(text=token, x0=0.0, x1=0.0, top=float(index), bottom=float(index), page=1)
            for token in stripped.split()
        ]
        lines.append(TextLine(words, page=1))

    return ParsedDocument(
        full_text=text,
        lines=lines,
        page_count=page_count,
        has_usable_text_layer=True,
    )
