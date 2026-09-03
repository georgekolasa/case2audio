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
    if re.search(r"(?mi)^page\s+\d+\s*\|\s*.+$", narration):
        findings.append(
            QualityFinding(
                "ERROR",
                "PAGE_FOOTER",
                "A running page footer remains in the narration.",
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
                f"Marked {signals.marked_visuals} substantial figure(s) for visual review.",
            )
        )

    suspicious = sorted(
        set(re.findall(r"(?i)\b(?:roduct|[a-z]+driven|end-of[a-z]+)\b", narration))
    )
    if suspicious:
        sample = ", ".join(suspicious[:5])
        findings.append(
            QualityFinding(
                "WARN",
                "SUSPICIOUS_WORDS",
                f"Review possible joined or damaged words: {sample}.",
            )
        )

    return QualityReport(tuple(findings))
