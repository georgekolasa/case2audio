"""Cheap narration checks that run before a paid Polly request."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Severity = Literal["INFO", "WARN", "ERROR"]


@dataclass(frozen=True)
class ExtractionSignals:
    """Counts from extraction that plain narration text cannot recover later."""

    redacted_text_items: int = 0
    narrated_text_tables: int = 0
    omitted_data_tables: int = 0
    marked_visuals: int = 0
    omitted_citation_blocks: int = 0
    retained_explanatory_notes: int = 0
    removed_margin_blocks: int = 0
    removed_inline_markers: int = 0
    repaired_source_words: int = 0
    repaired_source_forms: int = 0
    removed_front_matter_blocks: int = 0
    omitted_table_notes: int = 0
    removed_visual_labels: int = 0


@dataclass(frozen=True)
class QualityFinding:
    severity: Severity
    code: str
    message: str


@dataclass(frozen=True)
class QualityReport:
    findings: tuple[QualityFinding, ...]

    @property
    def blocking(self) -> tuple[QualityFinding, ...]:
        return tuple(finding for finding in self.findings if finding.severity == "ERROR")

    def render(self) -> str:
        """Create a stable human-readable report for debug output and CI fixtures."""

        if self.blocking:
            status = "BLOCKED"
        elif any(finding.severity == "WARN" for finding in self.findings):
            status = "PASS WITH WARNINGS"
        else:
            status = "PASS"

        lines = [f"Narration quality: {status}"]
        if not self.findings:
            lines.append("- INFO CLEAN: No known extraction problems detected.")
        for finding in self.findings:
            lines.append(f"- {finding.severity} {finding.code}: {finding.message}")
        return "\n".join(lines) + "\n"


def assess_narration(
    narration: str,
    *,
    signals: ExtractionSignals,
    hidden_texts: tuple[str, ...] = (),
) -> QualityReport:
    """Report intentional omissions and block known unsafe narration."""

    findings: list[QualityFinding] = []
    for count, code, description in (
        (signals.removed_margin_blocks, "MARGIN_TEXT_REMOVED", "page-margin blocks"),
        (signals.removed_inline_markers, "INLINE_CITATIONS_REMOVED", "confirmed raised citations"),
    ):
        if count:
            findings.append(QualityFinding("INFO", code, f"Removed {count} {description}."))
    if signals.repaired_source_words:
        findings.append(
            QualityFinding(
                "INFO",
                "SOURCE_WORD_JOINS",
                f"Repaired {signals.repaired_source_words} word splits confirmed by PDF spacing.",
            )
        )
    if signals.repaired_source_forms:
        findings.append(
            QualityFinding(
                "INFO",
                "SOURCE_PUNCTUATION_REPAIRED",
                f"Repaired {signals.repaired_source_forms} compounds/dashes confirmed by the PDF.",
            )
        )
    for count, code, description in (
        (signals.removed_front_matter_blocks, "FRONT_MATTER_REMOVED", "front-matter blocks"),
        (signals.omitted_table_notes, "TABLE_NOTES_OMITTED", "low-value table notes"),
        (signals.removed_visual_labels, "VISUAL_LABELS_REMOVED", "loose diagram labels"),
    ):
        if count:
            findings.append(QualityFinding("INFO", code, f"Removed {count} {description}."))
    if signals.omitted_citation_blocks:
        findings.append(
            QualityFinding(
                "INFO",
                "CITATIONS_OMITTED",
                f"Omitted {signals.omitted_citation_blocks} citation/source blocks; "
                f"retained {signals.retained_explanatory_notes} explanatory or ambiguous notes.",
            )
        )
    folded = " ".join(narration.split()).casefold()

    # A failed scrub is a privacy issue, so synthesis must never continue.
    if any(" ".join(hidden.split()).casefold() in folded for hidden in hidden_texts):
        findings.append(
            QualityFinding(
                "ERROR",
                "REDACTION_LEAK",
                "Selectable text hidden by a visual redaction is still present.",
            )
        )
    elif signals.redacted_text_items:
        findings.append(
            QualityFinding(
                "INFO",
                "REDACTION_SCRUBBED",
                f"Removed {signals.redacted_text_items} hidden text item(s) under black boxes.",
            )
        )

    # This combined footer form previously slipped through Docling's BODY classification.
    if re.search(r"(?mi)^.*(?:\bpage\s+\d+\s*\||\|\s*page\s+\d+\b).*$", narration):
        findings.append(
            QualityFinding(
                "ERROR",
                "PAGE_FOOTER",
                "A running page footer remains in the narration.",
            )
        )

    # PDF icon fonts can decode arrows as private-use characters that Polly cannot say.
    if re.search(r"[\ue000-\uf8ff]", narration):
        findings.append(
            QualityFinding(
                "ERROR",
                "UNSPOKEN_GLYPHS",
                "Private-use PDF glyphs remain; inspect the source figure before synthesis.",
            )
        )

    if signals.narrated_text_tables:
        findings.append(
            QualityFinding(
                "INFO",
                "TEXT_TABLES",
                f"Narrated {signals.narrated_text_tables} compact text table(s).",
            )
        )
    if signals.omitted_data_tables:
        findings.append(
            QualityFinding(
                "WARN",
                "DATA_TABLES",
                f"Marked {signals.omitted_data_tables} dense data table(s) for visual review.",
            )
        )
    if signals.marked_visuals:
        findings.append(
            QualityFinding(
                "WARN",
                "VISUALS",
                f"Marked {signals.marked_visuals} content figure(s) for visual review.",
            )
        )

    suspicious = sorted(set(re.findall(r"(?i)\b(?:roduct|[a-z]+driven|end-of[a-z]+)\b", narration)))
    if suspicious:
        sample = ", ".join(suspicious[:5])
        findings.append(
            QualityFinding(
                "WARN",
                "SUSPICIOUS_WORDS",
                f"Review possible joined or damaged words: {sample}.",
            )
        )

    # Flag uncertainty instead of guessing at numbers, broken words, or unusual proper names.
    checks = (
        (
            r";\s+[b-hj-z]\s*\(",
            "DAMAGED_SENTENCE",
            "A stray letter interrupts a sentence; check the PDF for missing text.",
        ),
        (
            r"[.!?][\"'’”)]?\s+\d{1,3}(?=\s*(?:\n|$))|%\s+\d{1,3}\s+[a-z]",
            "POSSIBLE_INLINE_CITATIONS",
            "Possible citation markers remain; review the text.",
        ),
        (
            r"\b[a-z]{3,}\s+[b-df-hj-np-tv-z]\b",
            "POSSIBLE_SPLIT_WORDS",
            "Possible split words remain; review before synthesis.",
        ),
        (
            r"\b([A-Za-z]{5,})\s+([A-Z]{4,})\b",
            "POSSIBLE_DUPLICATED_WORDS",
            "Possible damaged or duplicated name; review the text.",
        ),
    )
    for pattern, code, description in checks:
        matches = list(re.finditer(pattern, narration))
        if code == "POSSIBLE_DUPLICATED_WORDS":
            matches = [m for m in matches if m[1].casefold().endswith(m[2].casefold())]
        if matches:
            sample = "; ".join(repr(m.group()) for m in matches[:3])
            findings.append(QualityFinding("WARN", code, f"{description} Examples: {sample}"))

    bylines = re.findall(r"(?m)^BY\s+.+$", narration)
    if len(bylines) > 1:
        findings.append(
            QualityFinding(
                "WARN", "REPEATED_BYLINES", "Multiple bylines remain; check for page furniture."
            )
        )

    return QualityReport(tuple(findings))
