"""Detect selectable PDF text hidden underneath visual redaction boxes."""

from __future__ import annotations

import ctypes
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RedactionScan:
    """Only counts and text needed to keep hidden content out of every output."""

    hidden_texts: tuple[str, ...] = ()
    redaction_boxes: int = 0


def scan_visual_redactions(pdf_path: Path) -> RedactionScan:
    """Find opaque dark rectangles covering selectable text in a PDF."""

    # Docling already depends on PDFium; importing lazily keeps ordinary CLI help fast.
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    hidden: list[str] = []
    redaction_boxes = 0
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        for page in pdf:
            text_page = page.get_textpage()
            try:
                objects = list(page.get_objects(textpage=text_page))
                boxes = [
                    obj.get_bounds()
                    for obj in objects
                    if obj.type == pdfium_c.FPDF_PAGEOBJ_PATH
                    and _is_redaction_box(obj, page, pdfium_c)
                ]
                redaction_boxes += len(boxes)

                for obj in objects:
                    if obj.type != pdfium_c.FPDF_PAGEOBJ_TEXT:
                        continue
                    text = obj.extract().strip()
                    # Tiny marks and empty positioning objects are not sensitive prose.
                    if len(text) < 2:
                        continue
                    # White labels on dark design elements are visible, not redacted.
                    if not _has_dark_fill(obj, pdfium_c):
                        continue
                    if any(_coverage(obj.get_bounds(), box) >= 0.60 for box in boxes):
                        hidden.append(text)
            finally:
                text_page.close()
    finally:
        pdf.close()

    # Longest-first prevents a shorter overlapping fragment from defeating a full replacement.
    unique = tuple(sorted(set(hidden), key=len, reverse=True))
    return RedactionScan(hidden_texts=unique, redaction_boxes=redaction_boxes)


def scrub_hidden_text(value: str, hidden_texts: Iterable[str]) -> str:
    """Replace visually redacted strings everywhere, including debug artifacts."""

    for hidden in hidden_texts:
        # Docling may normalize spaces differently from PDFium's text objects.
        words = hidden.split()
        if not words:
            continue
        pattern = re.compile(r"\s+".join(re.escape(word) for word in words), re.IGNORECASE)
        value = pattern.sub("[redacted]", value)
    return value


def scrub_hidden_values(value: Any, hidden_texts: tuple[str, ...]) -> Any:
    """Recursively sanitize Docling's JSON-shaped export without mutating its model."""

    if isinstance(value, str):
        return scrub_hidden_text(value, hidden_texts)
    if isinstance(value, list):
        return [scrub_hidden_values(item, hidden_texts) for item in value]
    if isinstance(value, dict):
        return {key: scrub_hidden_values(item, hidden_texts) for key, item in value.items()}
    return value


def _is_redaction_box(obj, page, pdfium_c) -> bool:
    """Reject ordinary rules, borders, and large dark page backgrounds."""

    left, bottom, right, top = obj.get_bounds()
    width = right - left
    height = top - bottom
    page_width, page_height = page.get_size()

    # Underlines are commonly dark paths; real black boxes have meaningful height and area.
    if width < 6 or height < 4 or width * height < 50:
        return False
    # A dark page design is not a redaction and should not hide the whole document.
    if width * height > page_width * page_height * 0.25:
        return False

    fill_mode = ctypes.c_int()
    stroke = ctypes.c_int()
    if not pdfium_c.FPDFPath_GetDrawMode(obj.raw, fill_mode, stroke) or fill_mode.value == 0:
        return False

    return _has_dark_fill(obj, pdfium_c, maximum_channel=40, minimum_alpha=200)


def _has_dark_fill(
    obj,
    pdfium_c,
    *,
    maximum_channel: int = 100,
    minimum_alpha: int = 100,
) -> bool:
    red, green, blue, alpha = (ctypes.c_uint() for _ in range(4))
    if not pdfium_c.FPDFPageObj_GetFillColor(obj.raw, red, green, blue, alpha):
        return False
    return (
        max(red.value, green.value, blue.value) <= maximum_channel
        and alpha.value >= minimum_alpha
    )


def _coverage(subject: tuple[float, ...], cover: tuple[float, ...]) -> float:
    """Return how much of one rectangle is covered by another."""

    left = max(subject[0], cover[0])
    bottom = max(subject[1], cover[1])
    right = min(subject[2], cover[2])
    top = min(subject[3], cover[3])
    if right <= left or top <= bottom:
        return 0.0
    subject_area = max((subject[2] - subject[0]) * (subject[3] - subject[1]), 1.0)
    return (right - left) * (top - bottom) / subject_area
