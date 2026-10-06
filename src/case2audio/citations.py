"""Omit source lists from speech without discarding explanatory notes."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, replace

from .reading_order import TextBlock

_REFERENCE_HEADING = re.compile(
    r"^(?:(?:selected|bibliographic)\s+)?(?:references|bibliography|works cited|"
    r"literature cited|citations|end\s*notes)(?:\s*\(continued\))?\s*[:.]?$",
    re.I,
)
_NOTE_MARKER = re.compile(r"^\s*(?:\[?\d+\]?[.)]?|[ivxlcdm]+[.)]?|[*†‡]+)\s+")
# Exhibit captions often put a decorative bullet before Source:, especially after OCR.
_SOURCE_PREFIX = r"\s*(?:[·•▪◦]\s*)?sources?"
_SOURCE = re.compile(rf"^{_SOURCE_PREFIX}\s*:\s*", re.I)
_SOURCE_LABEL = re.compile(rf"^{_SOURCE_PREFIX}\s*:?$", re.I)
_YEAR = re.compile(r"\b(?:18|19|20)\d{2}\b")
_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_EXPLANATION = re.compile(
    r"^(?:this|these|the|in|because|although|however|we|our|for example|"
    r"figures|amounts|values|percentages|note|notes)\b",
    re.I,
)
# Demand the entire clause, rather than deleting any parentheses that happen to contain a year.
_AUTHOR_YEAR = re.compile(
    r"^(.+?),\s*((?:18|19|20)\d{2}[a-z]?)"
    r"(?:\s*[,/&–-]\s*(?:(?:18|19|20)\d{2}[a-z]?|[a-z]))*"
    r"(?:\s*[,;:]\s*(?:pp?\.\s*)?\d+(?:\s*[-–]\s*\d+)?)?\.?$"
)
_NAME_WORD = re.compile(r"[^\W\d_]+(?:[-’'][^\W\d_]+)*", re.UNICODE)
_NAME_CONNECTORS = {"and", "et", "al", "van", "von", "de", "der", "den", "del", "la", "di", "v"}
_DATE_OR_UNIT = re.compile(
    r"^(?:January|February|March|April|May|June|July|August|September|October|November|"
    r"December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|FY|USD|EUR|GBP)\.?$",
    re.I,
)


def _author_year_sources(markdown: str) -> frozenset[tuple[str, str]]:
    """Collect first-author/year evidence only from explicitly headed reference lists."""

    sources = set()
    in_references = False
    for line in html.unescape(markdown).splitlines():
        heading = re.match(r"^\s*#{1,6}\s+(.+)", line)
        if heading:
            in_references = bool(_REFERENCE_HEADING.fullmatch(heading[1].strip()))
        if not in_references or heading:
            continue
        line = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", line).lstrip("[")
        year = re.search(r"\b(?:18|19|20)\d{2}[a-z]?\b", line)
        if year:
            # APA initials follow the first comma; organizational authors usually have none.
            first_author = line[: year.start()].split(",", 1)[0].rstrip(" .(")
            sources.add((_author_key(first_author), year[0]))
    return frozenset(sources)


def _author_key(author: str) -> str:
    # Spacing and hyphen variants must not hide an otherwise exact bibliography match.
    return re.sub(r"\W+", "", author).casefold()


def _is_author_year_clause(clause: str, sources: frozenset[tuple[str, str]]) -> bool:
    clause = " ".join(clause.split())
    match = _AUTHOR_YEAR.fullmatch(clause)
    if not match:
        return False
    authors = re.sub(r"^(?:see(?: also)?|cf\.|e\.g\.,)\s+", "", match[1], flags=re.I)
    if _DATE_OR_UNIT.fullmatch(authors):
        return False
    words = _NAME_WORD.findall(authors)
    # Names, initials and organizations are allowed; prose, numbers and formulas are not.
    residue = _NAME_WORD.sub("", authors)
    if not words or residue.strip(" .,\t&"):
        return False
    if not any(word[0].isupper() for word in words) or not all(
        word[0].isupper() or word in _NAME_CONNECTORS for word in words
    ):
        return False
    first_author = re.split(r",|&|\band\b|\bet\s+al\b", authors, maxsplit=1)[0]
    if (_author_key(first_author), match[2]) in sources:
        return True
    # A bare name/year also fits data labels and place/date pairs. Keep it without evidence.
    return bool(re.search(r",|&|\band\b|\bet\s+al\b", authors))


def strip_parenthetical_citations(
    text: str, *, reference_markdown: str = ""
) -> tuple[str, int]:
    """Remove author-year source clauses, retaining explanatory text in mixed parentheses."""

    removed = 0
    sources = _author_year_sources(reference_markdown)

    def clean(match: re.Match[str]) -> str:
        nonlocal removed
        clauses = match[1].split(";")
        kept = [clause.strip() for clause in clauses if not _is_author_year_clause(clause, sources)]
        count = len(clauses) - len(kept)
        if not count:
            return match[0]
        removed += count
        dash = match[2] or ""
        if kept:
            return "(" + "; ".join(kept) + ")" + dash
        # A dash following a citation still separates real prose and needs a spoken pause.
        if dash:
            return " - "
        # Removing a citation between adjacent words must not create a new joined word.
        before = text[max(0, match.start() - 1) : match.start()]
        after = text[match.end() : match.end() + 1]
        return " " if before.isalnum() and after.isalnum() else ""

    # Whitespace inside a citation can include Docling paragraph/page breaks.
    cleaned = re.sub(r"\(([^()]*)\)([ \t]*[–—-])?", clean, text)
    if removed:
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        cleaned = re.sub(r"[ \t]+([,.;:!?])", r"\1", cleaned)
        cleaned = re.sub(r"[ \t]+\)", ")", cleaned)
        cleaned = re.sub(r" *\n *", "\n", cleaned).lstrip(" \t")
    return cleaned, removed


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
    # Initials and abbreviations are not sentence boundaries: demand actual methodology prose.
    for match in re.finditer(r"[.!?]\s+(?=(.+))", remainder):
        tail = match.group(1)
        if re.match(
            r"(?:[A-Z]{2,8}\s+(?:is|are|was|were)\b|"
            r"(?:The\s+(?:sample|analysis|data|figures)|Figures|Values|Amounts)\b)",
            tail,
        ):
            return _clean_explanation(tail) or None
    return None


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
        if quoted_title and _YEAR.search(sentence) and is_citation_only(sentence):
            continue
        retained.append(sentence)
    return " ".join(retained).strip()


def is_citation_only(text: str) -> bool:
    """Return whether one detached note is only a source citation.

    This stays conservative because OCR cleanup uses its marker numbers as evidence
    before deleting flattened footnotes from otherwise real prose.
    """
    text = _without_marker(text)
    # Favor retaining an explanation when classification is ambiguous.
    if _EXPLANATION.match(text):
        return False
    if re.match(r"^(?:ibid\.?|op\.\s*cit\.)\b", text, re.I):
        return True
    if _URL.search(text) or re.search(r"\bdoi\s*:", text, re.I):
        return True
    # Named authors/publishers followed by a title and year or page number are citation-shaped.
    author = re.match(r"^([^,\n]{1,100}),", text)
    # A capitalized sentence plus a comma and year is not evidence of a bibliography entry.
    # Names/publishers use capitalized words, initials, and a few ordinary name connectors.
    if author:
        words = re.findall(r"[\w’'-]+", author[1])
        author = bool(words) and all(
            word[0].isupper() or word in {"and", "of", "the", "de", "van", "von"} for word in words
        )
    recommendation = re.match(r"^(?:see|for (?:a|an|more|further))\b", text, re.I)
    return bool(
        (author or recommendation)
        and (_YEAR.search(text) or re.search(r",\s*(?:pp?\.\s*)?\d+(?:[-–]\d+)?\.?$", text))
    )


def filter_citation_blocks(blocks: list[TextBlock]) -> CitationResult:
    """Filter an ordered narrative stream; bibliography scope ends at the next section."""

    blocks = _join_source_labels(blocks)
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
                and sum(is_citation_only(item) for item in next_texts) >= 2
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
                if _EXPLANATION.match(candidate) and not is_citation_only(candidate):
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
            _URL.fullmatch(text) or (source_needs_entry and is_citation_only(text))
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
            if is_citation_only(text):
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


def _join_source_labels(blocks: list[TextBlock]) -> list[TextBlock]:
    """Keep a split Source label and its entry together before source classification."""

    result: list[TextBlock] = []
    index = 0
    while index < len(blocks):
        block = blocks[index]
        if _SOURCE_LABEL.fullmatch(block.text):
            block = replace(block, text="Source:")
            if index + 1 < len(blocks):
                following = blocks[index + 1]
                # The colon provides strong evidence even for undated company documents.
                if (
                    following.page == block.page
                    and following.label == "text"
                    and (
                        following.text.lstrip().startswith(":")
                        or is_citation_only(following.text)
                    )
                ):
                    block = replace(block, text="Source: " + following.text.lstrip(" :"))
                    index += 1
        # One source line can have several same-page character spans, split at a font change.
        # Physical adjacency distinguishes the remaining publisher name from new body prose.
        while _SOURCE.match(block.text) and index + 1 < len(blocks):
            following = blocks[index + 1]
            if not (
                following.page == block.page
                and following.label == "text"
                and abs(following.top - block.top) <= 3
                and -2 <= following.left - block.right <= 24
            ):
                break
            block = replace(
                block,
                text=block.text.rstrip() + " " + following.text.lstrip(),
                right=max(block.right, following.right),
            )
            index += 1
        result.append(block)
        index += 1
    return result
