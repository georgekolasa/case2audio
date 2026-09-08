"""Convert Docling Markdown into text that sounds natural when spoken."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from .boilerplate import strip_publishing_boilerplate
from .word_repairs import repair_words

# These are intentionally narrow: deleting real case prose is worse than leaving minor noise.
DEFAULT_DROP_PATTERNS = (
    r"^id\s*#\s*\S+\s*$",
    r"^published on\s+.+$",
    r"^this document is authorized for use only by\b.*$",
    r"^article reprint no\.\s*\S+\s*$",
    r"^a newsletter from .* publishing\b.*$",
    r"^decision-making and communication strategies that deliver results$",
    r"^negotiation$",
    r"^for reprint and subscription information\b.*$",
    r"^for customized and quantity orders of reprints\b.*$",
    r"^for a complete list of .* newsletters\b.*$",
)

_IMAGE_RE = re.compile(r"!\[[^]]*]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^]]+)]\([^)]*\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_LIST_RE = re.compile(r"^\s*(?:(?:[-*+]\s*)+|\d+[.)]\s+)")
_TABLE_DIVIDER_RE = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_PAGE_NUMBER_RE = re.compile(r"^\s*(?:page\s+)?\d{1,3}\s*$", re.IGNORECASE)
_RUNNING_FOOTER_RE = re.compile(r"^page\s+\d+\s*\|\s*.+$", re.IGNORECASE)
# A private-use marker survives whitespace cleanup and preserves deliberate spoken breaks.
_HARD_BREAK = "\ue000"


@dataclass(frozen=True)
class CleanerOptions:
    """Narration policy kept separate from PDF extraction."""

    table_mode: str = "smart"
    extra_drop_patterns: tuple[str, ...] = ()


def clean_markdown(markdown: str, options: CleanerOptions | None = None) -> str:
    """Return plain narration text from Docling-flavored Markdown."""

    options = options or CleanerOptions()
    if options.table_mode not in {"smart", "skip", "linearize"}:
        raise ValueError("table_mode must be 'smart', 'skip', or 'linearize'")

    # Decode entities before stripping tags so things like ampersands remain speakable.
    text = html.unescape(markdown.replace("\r\n", "\n").replace("\r", "\n"))
    text = _IMAGE_RE.sub("", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _HTML_TAG_RE.sub("", text)
    text = strip_publishing_boilerplate(text)

    all_drop_patterns = (*DEFAULT_DROP_PATTERNS, *options.extra_drop_patterns)
    drop_patterns = tuple(re.compile(pattern, re.IGNORECASE) for pattern in all_drop_patterns)

    output: list[str] = []
    intro_lines: set[str] = set()
    in_intro = True
    in_fenced_block = False
    for raw_line in text.splitlines():
        line = raw_line.strip()

        # Code fences and their contents are usually extraction artifacts in business PDFs.
        if line.startswith("```"):
            in_fenced_block = not in_fenced_block
            continue
        if in_fenced_block:
            continue

        if not line:
            output.append("")
            continue
        if _PAGE_NUMBER_RE.fullmatch(line):
            continue
        # Docling can mislabel a combined running footer as a real section heading.
        if _RUNNING_FOOTER_RE.fullmatch(_HEADING_RE.sub("", line)):
            continue
        if any(pattern.search(line) for pattern in drop_patterns):
            continue

        if _TABLE_DIVIDER_RE.fullmatch(line):
            continue
        if _TABLE_ROW_RE.fullmatch(line):
            if options.table_mode in {"smart", "skip"}:
                continue
            # Pipes sound terrible; short pauses preserve the row's meaning.
            line = "; ".join(cell.strip() for cell in line.strip("|").split("|") if cell.strip())
            line += _HARD_BREAK

        line = _HEADING_RE.sub("", line)
        line = _LIST_RE.sub("", line)
        line = _strip_markdown_emphasis(line)

        # Cover pages and later footers often repeat the article title and byline.
        normalized = re.sub(r"\s+", " ", line).casefold()
        if normalized in intro_lines:
            continue
        if in_intro:
            intro_lines.add(normalized)
            if len(line) > 180:
                in_intro = False
        output.append(line)

    text = "\n".join(output)

    # Join words split only because a PDF line ended; preserve real compounds.
    text = re.sub(r"(?<=[A-Za-z])-\n+\s*(?=[a-z])", "", text)
    # Docling already gives paragraph breaks, so single newlines are safe to turn into spaces.
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    # Restore deliberate table row breaks after ordinary prose lines have been joined.
    text = re.sub(rf"{_HARD_BREAK}\s*", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = repair_words(text)
    # A space only before a hyphen is a PDF layout error, unlike a spaced em-dash substitute.
    text = re.sub(r"(?<=[A-Za-z])\s+-(?=[A-Za-z])", "-", text)
    # Voices read a bare range dash inconsistently; "to" is unambiguous for year ranges.
    text = re.sub(
        r"\b((?:FY)?(?:19|20)\d{2})\s+[–—-]\s+((?:FY)?(?:19|20)\d{2})\b",
        r"\1 to \2",
        text,
    )
    # Repair a few high-confidence PDF joins without applying risky general spellcheck.
    text = re.sub(r"\broduct\b", "product", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\b(market|data|customer|technology|performance|mission|purpose|value)driven\b",
        r"\1-driven",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bend-of(?=(?:year|life|course|day)\b)",
        "end-of-",
        text,
        flags=re.IGNORECASE,
    )
    # These words after a lost em dash are discourse, not compound-word suffixes.
    # Leave forms such as "up-and-comers" alone: the second hyphen proves it is a compound.
    text = re.sub(
        r"(?<=\w)-(?=(?:and|but|or|straight|was|were)\b(?!-))",
        " - ",
        text,
        flags=re.IGNORECASE,
    )
    # The source pattern was "deadlines-name a setback"; do not break real "no-name" compounds.
    text = re.sub(r"(?<=s)-(?=name\b)", " - ", text, flags=re.I)
    # A lower-case plural followed by a capitalized name usually marks a lost em dash.
    text = re.sub(r"(?<=s)-(?=[A-Z])", " - ", text)
    text = re.sub(r"\bman power\b", "manpower", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+:\s*", ": ", text)
    text = re.sub(r"(?m)^Exhibits\s+(?=Exhibit\b)", "Exhibits.\n\n", text)
    # A standalone emphasis marker before a parenthetical should not be spoken as punctuation.
    text = text.replace("*(", "(")
    # A following lowercase word distinguishes a broken drop cap from an all-caps heading.
    text = re.sub(r"(?m)^([A-Z])\s+([A-Z]{2,})(?=\s+[a-z])", r"\1\2", text)
    # PDF typography often leaves spaces around apostrophes and terminal punctuation.
    text = re.sub(r"\b([A-Za-z]+)\s+(['’]s)\b", r"\1\2", text)
    text = re.sub(r"\b([A-Za-z]+)\s+(['’])(?=\s|[,.])", r"\1\2", text)
    text = re.sub(r"\s+([,.])", r"\1", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    # Older PDFs can lose a dash at an italic boundary around this common phrase.
    text = re.sub(r"\bquestions(?=what, how, and why\b)", "questions - ", text, flags=re.I)
    # Contact details add little to an audiobook but often contain awkward punctuation.
    text = re.sub(
        r"[ \t]*They can be reached at\s+[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\.?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def _strip_markdown_emphasis(line: str) -> str:
    """Remove common Markdown punctuation without eating apostrophes or dashes."""

    line = re.sub(r"(?<!\\)(?:\*\*|__)(.+?)(?<!\\)(?:\*\*|__)", r"\1", line)
    line = re.sub(r"(?<!\\)(?:\*|_)(.+?)(?<!\\)(?:\*|_)", r"\1", line)
    line = line.replace("\\*", "*").replace("\\_", "_")
    return line
