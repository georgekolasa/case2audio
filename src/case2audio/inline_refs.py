"""Use original PDF geometry to identify raised citation markers, not ordinary numbers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .source_forms import SourceForm, punctuation_forms


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
    source_forms: tuple[SourceForm, ...] = ()
    # Whole-page text lets us repair Docling damage only when the PDF confirms the answer.
    page_texts: tuple[str, ...] = ()


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
    if not re.search(r"(?:[.!?,;:%][\"'’”)\s]*|\b(?:19|20)\d{2})\s*$", before):
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
    forms = []
    page_texts = []
    with pdfium.PdfDocument(path) as pdf:
        for page_no in range(len(pdf)):
            page = pdf[page_no]
            text_page = page.get_textpage()
            try:
                page_text = text_page.get_text_range()
                page_texts.append(page_text)
                forms.extend(punctuation_forms(page_text, page_no + 1))
                previous = None
                prose = None
                # Raised references often arrive as separate objects: "61", comma, "62".
                # Keep the prose anchor alive until the whole citation cluster is consumed.
                citation_anchor = None
                citation_bounds = None
                citation_right = None
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
                    if (
                        marker is None
                        and citation_anchor
                        and citation_bounds
                        and citation_right is not None
                        and re.fullmatch(r"\d{1,3}|[ivxlcdm]{1,4}", text.strip(), re.I)
                    ):
                        token = text.strip()
                        left, bottom, _, top = current[1]
                        _, base_bottom, _, base_top = citation_bounds
                        base_height = base_top - base_bottom
                        # A second small, raised number next to the first belongs to the
                        # same citation cluster even when no comma object is encoded.
                        if (
                            0 <= left - citation_right <= 10
                            and 0 < top - bottom < base_height * 0.8
                            and base_bottom + base_height * 0.15 < bottom < base_top
                        ):
                            marker = token
                    if marker:
                        # A short local anchor avoids editing a different occurrence on the page.
                        if citation_anchor:
                            anchor = citation_anchor
                        else:
                            anchor = " ".join(normalize_quotes(prose[0]).split()[-6:])
                            citation_anchor = anchor
                            citation_bounds = prose[1]
                        found.append(
                            RaisedReference(
                                page_no + 1, marker, anchor, current[1][0], current[1][1]
                            )
                        )
                        citation_right = current[1][2]
                    elif citation_anchor and re.fullmatch(r"[,;\s]+", text):
                        # Punctuation between raised markers is part of the cluster.
                        citation_right = current[1][2]
                    else:
                        citation_anchor = citation_bounds = citation_right = None
                    fragments = touching_word_fragments(previous, current) if previous else None
                    if fragments:
                        joins.append(
                            WordJoin(page_no + 1, *fragments, current[1][0], current[1][1])
                        )
                    # A font change can put the closing period in its own tiny object.
                    # Use the preceding prose's height, not the height of that dot.
                    if (
                        prose
                        and re.fullmatch(r"[.!?,;:'\"’”)\s]+", text.strip())
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
    return PdfTextEvidence(tuple(found), tuple(joins), tuple(forms), tuple(page_texts))


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


def repair_source_tokens(block, page_texts) -> tuple[str, int, int]:
    """Repair broken word/number spacing only when the same PDF page proves the form."""

    if not 1 <= block.page <= len(page_texts):
        return block.text, 0, 0

    source = normalize_quotes(page_texts[block.page - 1])
    source = re.sub(r"\s+", " ", source)
    source_folded = source.casefold()
    text = block.text
    repaired_words = 0

    # Docling can insert a space inside a word even though PDFium sees one clean token.
    # Work one boundary at a time so "smart casu al" can repair only "casu al".
    while True:
        replacements = []
        words = list(re.finditer(r"[A-Za-z]+", text))
        for left, right in zip(words, words[1:], strict=False):
            if not re.fullmatch(r"[ \t]+", text[left.end() : right.start()]):
                continue
            joined = left.group() + right.group()
            separated = (
                rf"(?<![A-Za-z]){re.escape(left.group())}\s+"
                rf"{re.escape(right.group())}(?![A-Za-z])"
            )
            if (
                re.search(rf"(?<![A-Za-z]){re.escape(joined)}(?![A-Za-z])", source, re.I)
                and not re.search(separated, source, re.I)
            ):
                replacements.append((left.start(), right.end(), joined))
        if not replacements:
            break
        # Reverse order keeps earlier spans stable; skip overlapping candidates defensively.
        boundary = len(text) + 1
        for start, end, joined in reversed(replacements):
            if end > boundary:
                continue
            text = text[:start] + joined + text[end:]
            boundary = start
            repaired_words += 1

    repaired_numbers = 0

    def join_percent(match: re.Match) -> str:
        nonlocal repaired_numbers
        joined = re.sub(r"\s+", "", match.group())
        if joined.casefold() not in source_folded:
            return match.group()
        repaired_numbers += 1
        return joined

    # A spaced digit inside a percentage changes the spoken value, so verify it exactly.
    text = re.sub(r"\b\d+(?:[ \t]+\d+)+%", join_percent, text)

    # Recover a dropped decimal digit when the page contains only one matching currency scale.
    source_amounts: dict[tuple[str, str], set[str]] = {}
    for match in re.finditer(
        r"([$€£])\s*(\d[\d,]*(?:\.\d+)?)\s+(thousand|million|billion|trillion)\b",
        source,
        re.I,
    ):
        source_amounts.setdefault((match[1], match[3].casefold()), set()).add(match[2])

    def repair_truncated_amount(match: re.Match) -> str:
        nonlocal repaired_numbers
        key = (match[1], match[3].casefold())
        candidates = source_amounts.get(key, set())
        integer = match[2].rstrip(".")
        candidates = {value for value in candidates if value.split(".", 1)[0] == integer}
        if len(candidates) != 1:
            return match.group()
        repaired_numbers += 1
        return f"{match[1]}{next(iter(candidates))} {match[3]}"

    text = re.sub(
        r"([$€£])\s*(\d[\d,]*\.)\s+(thousand|million|billion|trillion)\b",
        repair_truncated_amount,
        text,
        flags=re.I,
    )
    return text, repaired_words, repaired_numbers


def strip_inline_references(block, references, known_markers: set[str]):
    text = normalize_quotes(block.text)
    removed = 0
    grouped: dict[str, list[str]] = {}
    for ref in references:
        # Geometry plus a matching note or a document-wide citation sequence are required.
        if ref.page != block.page or ref.marker not in known_markers:
            continue
        if not (
            block.left - 3 <= ref.left <= block.right + 3
            and block.bottom - 3 <= ref.bottom <= block.top + 3
        ):
            continue
        grouped.setdefault(ref.anchor, []).append(ref.marker)

    for raw_anchor, markers in grouped.items():
        anchor = reference_anchor_pattern(raw_anchor)
        alternatives = "|".join(
            re.escape(marker) for marker in sorted(set(markers), key=len, reverse=True)
        )
        # Remove the whole raised cluster at once. Doing markers one-by-one leaves the
        # comma behind and prevents later markers from seeing the original prose anchor.
        pattern = rf"({anchor})(?P<tail>(?:\s*(?:,\s*)?(?:{alternatives}))+)(?![\w.\d])"

        def remove_cluster(
            match: re.Match,
            alternatives: str = alternatives,
            current_text: str = text,
        ) -> str:
            nonlocal removed
            removed += len(re.findall(rf"(?:{alternatives})", match.group("tail"), re.I))
            # The marker's following space is outside the matched cluster; restore it when
            # Docling placed the next sentence directly against the raised reference.
            needs_space = match.end() < len(current_text) and bool(
                re.match(r"[A-Za-z'\"]", current_text[match.end() :])
            )
            return match[1] + (" " if needs_space else "")

        text = re.sub(pattern, remove_cluster, text, count=1, flags=re.I)
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
