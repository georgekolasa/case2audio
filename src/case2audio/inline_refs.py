"""Use original PDF geometry to identify raised citation markers, not ordinary numbers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RaisedReference:
    page: int
    marker: str
    anchor: str
    left: float
    bottom: float


@dataclass(frozen=True)
class WordJoin:
    page: int
    left_word: str
    right_word: str
    left: float
    bottom: float


@dataclass(frozen=True)
class PdfTextEvidence:
    references: tuple[RaisedReference, ...]
    word_joins: tuple[WordJoin, ...]


def normalize_quotes(text: str) -> str:
    # Docling normalizes curly quotes; anchors must agree with both representations.
    return text.translate(str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'}))


def raised_reference(previous, current) -> str | None:
    """Require a small, raised token immediately after punctuated prose."""
    before, (pl, pb, pr, pt) = previous
    token, (left, bottom, right, top) = current
    token = token.strip()
    if not re.fullmatch(r"\d{1,3}|[ivxlcdm]{1,4}", token):
        return None
    # Exponents after variables/units and numbered entities are not reference markers.
    if not re.search(r"[.!?,;:%][\"'’”)]*\s*$", before):
        return None
    height = pt - pb
    if not (height > 0 and 0 < top - bottom < height * 0.8):
        return None
    if not (-1 <= left - pr <= 8 and pb + height * 0.15 < bottom < pt):
        return None
    return token


def touching_word_fragments(previous, current) -> tuple[str, str] | None:
    before, (_, pb, pr, pt) = previous
    after, (left, bottom, _, top) = current
    first = re.search(r"[A-Za-z]+$", before)
    second = re.match(r"[A-Za-z]+", after)
    if not first or not second:
        return None
    height = max(pt - pb, top - bottom)
    overlap = min(pt, top) - max(pb, bottom)
    # No encoded space AND a letter-sized gap: real spaces and line wraps must survive.
    if not (-0.5 <= left - pr <= height * 0.16 and overlap > min(pt - pb, top - bottom) * 0.5):
        return None
    return first.group(), second.group()


def scan_pdf_text_evidence(path: Path) -> PdfTextEvidence:
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    found = []
    joins = []
    with pdfium.PdfDocument(path) as pdf:
        for page_no in range(len(pdf)):
            page = pdf[page_no]
            text_page = page.get_textpage()
            try:
                previous = None
                prose = None
                for obj in page.get_objects(textpage=text_page):
                    if obj.type != raw.FPDF_PAGEOBJ_TEXT:
                        continue
                    text = obj.extract()
                    if not text.strip():
                        # A real space object breaks a word even if its visible gap is tiny.
                        if text:
                            previous = None
                        continue
                    current = (text, obj.get_bounds())
                    marker = raised_reference(prose, current) if prose else None
                    if marker:
                        # A short local anchor avoids editing a different occurrence on the page.
                        anchor = " ".join(normalize_quotes(prose[0]).split()[-6:])
                        found.append(
                            RaisedReference(
                                page_no + 1, marker, anchor, current[1][0], current[1][1]
                            )
                        )
                    fragments = touching_word_fragments(previous, current) if previous else None
                    if fragments:
                        joins.append(
                            WordJoin(page_no + 1, *fragments, current[1][0], current[1][1])
                        )
                    # A font change can put the closing period in its own tiny object.
                    # Use the preceding prose's height, not the height of that dot.
                    if (
                        prose
                        and re.fullmatch(r"[.!?,;:'\"’”)]+", text.strip())
                        and -1 <= current[1][0] - prose[1][2] <= 8
                        and prose[1][1] - 1 <= current[1][1] <= prose[1][3]
                    ):
                        pl, pb, pr, pt = prose[1]
                        cl, cb, cr, ct = current[1]
                        prose = (prose[0] + text, (pl, min(pb, cb), cr, max(pt, ct)))
                    else:
                        prose = current
                    previous = current
            finally:
                text_page.close()
                page.close()
    return PdfTextEvidence(tuple(found), tuple(joins))


def reference_sequence_markers(references) -> set[str]:
    # Some PDFs have no bibliography. A run of three raised, punctuated reference numbers
    # provides independent evidence of citation numbering without treating an exponent as a note.
    numbers = {int(r.marker) for r in references if r.marker.isdigit()}
    if not any({n, n + 1, n + 2} <= numbers for n in numbers):
        return set()
    return {str(n) for n in numbers}


def repair_source_word_joins(block, joins):
    text = block.text
    count = 0
    for join in joins:
        if join.page != block.page or not (
            block.left - 3 <= join.left <= block.right + 3
            and block.bottom - 3 <= join.bottom <= block.top + 3
        ):
            continue
        pattern = rf"\b({re.escape(join.left_word)})[ \t]*({re.escape(join.right_word)})\b"
        matches = list(re.finditer(pattern, text))
        # A paragraph can contain both 'in to' and 'into'. Block geometry alone cannot tell
        # which occurrence owns the source evidence, so leave repeated candidates untouched.
        if len(matches) != 1:
            continue
        match = matches[0]
        joined = match[1] + match[2]
        if match.group() != joined:
            text = text[: match.start()] + joined + text[match.end() :]
            count += 1
    return text, count


def strip_inline_references(block, references, known_markers: set[str]):
    text = normalize_quotes(block.text)
    removed = 0
    for ref in references:
        # Geometry plus a matching note or a document-wide citation sequence are required.
        if ref.page != block.page or ref.marker not in known_markers:
            continue
        if not (
            block.left - 3 <= ref.left <= block.right + 3
            and block.bottom - 3 <= ref.bottom <= block.top + 3
        ):
            continue
        anchor = reference_anchor_pattern(ref.anchor)
        pattern = rf"({anchor})\s*{re.escape(ref.marker)}(?![\w.\d])"
        text, count = re.subn(pattern, r"\1", text, count=1)
        removed += count
    return (text if removed else block.text), removed


def reference_anchor_pattern(anchor: str) -> str:
    """Build one tolerant PDF-to-Docling anchor pattern for placement and removal."""

    pattern = r"\s+".join(re.escape(word) for word in normalize_quotes(anchor).split())
    # Some PDF backends turn double quotation marks into apostrophes.
    pattern = re.sub(r"[\"']", lambda _: r"\s*[\"']", pattern)
    # Italic boundaries can insert a space before a closing period in Docling text.
    return pattern.replace(r"\.", r"\s*\.")


def contains_reference_anchor(text: str, reference) -> bool:
    """Match a cleaned paragraph after its raised marker has already been removed."""

    return re.search(reference_anchor_pattern(reference.anchor), normalize_quotes(text)) is not None
