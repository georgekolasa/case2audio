"""Omit source lists from speech without discarding explanatory notes."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from .reading_order import TextBlock

_REFERENCE_HEADING = re.compile(
    r"^(?:(?:selected|bibliographic)\s+)?(?:references|bibliography|works cited|"
    r"literature cited|citations|end\s*notes)(?:\s*\(continued\))?\s*[:.]?$",
    re.I,
)
_NOTE_MARKER = re.compile(r"^\s*(?:\[?\d+\]?[.)]?|[ivxlcdm]+[.)]?|[*†‡]+)\s+")
_SOURCE = re.compile(r"^sources?\s*:\s*", re.I)
_YEAR = re.compile(r"\b(?:18|19|20)\d{2}\b")
_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_EXPLANATION = re.compile(
    r"^(?:this|these|the|in|because|although|however|we|our|for example|"
    r"figures|amounts|values|percentages|note|notes)\b",
    re.I,
)


@dataclass(frozen=True)
class CitationResult:
    blocks: list[TextBlock]
    omitted: int = 0
    explanations: int = 0


def _without_marker(text: str) -> str:
    return _NOTE_MARKER.sub("", text.strip(), count=1)


def _explanation_tail(text: str) -> str | None:
    # A note that starts as an explanation should be kept whole, not trimmed to a later sentence.
    if _EXPLANATION.match(_without_marker(text)):
        return None
    # Publishers often append important qualifications after an otherwise disposable citation.
    match = re.search(r"\bNotes?(?: on [^:]{1,60})?\s*:\s*(.+)", text, re.I | re.S)
    if match:
        return _clean_explanation(match.group(1).strip())
    # Keep a clearly explanatory sentence even if the citation omitted an explicit 'Note:' label.
    match = re.search(
        r"[.!?]\s+((?:This|These|In this|For example|Because|However|We|Our|Figures)\b.+)",
        text,
        re.S,
    )
    return _clean_explanation(match.group(1).strip()) if match else None


def _source_explanation(text: str, source_end: int) -> str | None:
    """Keep methodology after an inline source while dropping the attribution sentence."""

    remainder = text[source_end:].strip()
    # The first sentence names the source; later prose often defines a metric or limitation.
    match = re.search(r"[.!?]\s+(.+)", remainder, re.S)
    if not match:
        return None
    explanation = _clean_explanation(match.group(1).strip())
    return explanation or None


def _clean_explanation(text: str) -> str:
    """Trim source/editorial sentences only inside already identified notes."""
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    retained = []
    for sentence in sentences:
        # These describe provenance or manuscript preparation, not the case's argument.
        if re.match(
            r"^(?:This statement, and all others by .+ are from an interview\b|"
            r"Used with attribution as required by\b|"
            r"The previous draft['’]s statement\b)",
            sentence,
            re.I,
        ):
            continue
        # A date alone is not a citation: require a quoted title plus source-like structure.
        quoted_title = re.search(r'[,;]\s*["“].+?["”]', sentence)
        if quoted_title and _YEAR.search(sentence) and _citation_only(sentence):
            continue
        retained.append(sentence)
    return " ".join(retained).strip()


def _citation_only(text: str) -> bool:
    text = _without_marker(text)
    # Favor retaining an explanation when classification is ambiguous.
    if _EXPLANATION.match(text):
        return False
    if re.match(r"^(?:ibid\.?|op\.\s*cit\.)\b", text, re.I):
        return True
    if _URL.search(text) or re.search(r"\bdoi\s*:", text, re.I):
        return True
    # Named authors/publishers followed by a title and year or page number are citation-shaped.
    author = re.match(r"^[A-Z][^.!?\n]{1,100},", text)
    recommendation = re.match(r"^(?:see|for (?:a|an|more|further))\b", text, re.I)
    return bool(
        (author or recommendation)
        and (_YEAR.search(text) or re.search(r",\s*(?:pp?\.\s*)?\d+(?:[-–]\d+)?\.?$", text))
    )


def filter_citation_blocks(blocks: list[TextBlock]) -> CitationResult:
    """Filter an ordered narrative stream; bibliography scope ends at the next section."""

    output: list[TextBlock] = []
    omitted = explanations = 0
    reference_section = False
    reference_header: TextBlock | None = None
    explanation_header_added = False
    source_continuation = False
    source_needs_entry = False
    for index, block in enumerate(blocks):
        text = block.text.strip()
        if block.label == "section_header":
            # A generic 'Notes' heading alone is too broad: it may introduce substantive prose.
            next_texts = [
                item.text
                for item in blocks[index + 1 : index + 4]
                if item.label != "section_header"
            ]
            citation_notes = (
                text.casefold().rstrip(":") == "notes"
                and sum(_citation_only(item) for item in next_texts) >= 2
            )
            if _REFERENCE_HEADING.fullmatch(text) or citation_notes:
                reference_section = True
                reference_header = block
                explanation_header_added = False
                omitted += 1
                continue
            # Resume for an appendix, exhibits, or any other actual section following references.
            reference_section = False
            source_continuation = False

        if reference_section:
            explanation = _explanation_tail(text)
            if explanation is None and block.label == "footnote":
                candidate = _without_marker(text)
                if _EXPLANATION.match(candidate) and not _citation_only(candidate):
                    explanation = _clean_explanation(candidate)
            if explanation:
                if not explanation_header_added and reference_header is not None:
                    output.append(replace(reference_header, text="Explanatory notes"))
                    explanation_header_added = True
                # Identical retained tails can come from separate adjacent bibliography entries.
                if not output or output[-1].text != explanation:
                    output.append(replace(block, text=explanation, label="text"))
                    explanations += 1
            omitted += 1
            continue

        source = _SOURCE.match(text)
        if source:
            # A source label and its URL are sometimes separate Docling blocks.
            source_continuation = True
            source_needs_entry = not text[source.end() :].strip() or text.endswith(",")
            explanation = _explanation_tail(text) or _source_explanation(text, source.end())
            if explanation:
                output.append(replace(block, text=explanation, label="text"))
                explanations += 1
            omitted += 1
            continue
        if source_continuation and (
            _URL.fullmatch(text) or (source_needs_entry and _citation_only(text))
        ):
            # Only consume one split citation entry; following prose can also contain dates.
            source_needs_entry = False
            omitted += 1
            continue
        source_continuation = False

        if block.label == "footnote":
            explanation = _explanation_tail(text)
            if explanation:
                output.append(replace(block, text=explanation, label="text"))
                omitted += 1
                explanations += 1
                continue
            if _citation_only(text):
                omitted += 1
                continue
            # Retain definitions and caveats, without speaking their detached footnote markers.
            cleaned = _clean_explanation(_without_marker(text))
            if cleaned:
                # Make a detached footnote intelligible when it follows its referring paragraph.
                output.append(replace(block, text=f"Explanatory note: {cleaned}", label="text"))
                explanations += 1
            else:
                omitted += 1
            continue
        output.append(block)
    return CitationResult(output, omitted, explanations)
