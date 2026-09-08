"""Recover missing compound hyphens and prose dashes from the PDF's own text."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SourceForm:
    page: int
    parts: tuple[str, ...]
    separators: tuple[str, ...]


def punctuation_forms(text: str, page: int) -> tuple[SourceForm, ...]:
    # PDFium exposes some line-ending hyphens as U+FFFE; this is evidence, not output text.
    pattern = r"\b[A-Za-z]+(?:[-–—\ufffe]\s*[A-Za-z0-9]+)+\b"
    forms = []
    for match in re.finditer(pattern, text):
        # Suspended compounds such as "health- and moderation-conscious" keep their space.
        if re.search(r"[-\ufffe][ \t]+(?:and|or)\b", match[0]):
            continue
        tokens = re.split(r"([-–—\ufffe])\s*", match[0])
        parts = tuple(tokens[::2])
        separators = tuple(" - " if s == "—" else "-" for s in tokens[1::2])
        forms.append(SourceForm(page, parts, separators))
    return tuple(dict.fromkeys(forms))


def repair_source_forms(block, forms) -> tuple[str, int]:
    text = block.text
    repaired = 0
    for form in forms:
        if form.page != block.page:
            continue
        pattern = r"\b" + r"[ \t]*[-–—]?[ \t]*".join(map(re.escape, form.parts)) + r"\b"
        matches = list(re.finditer(pattern, text))
        # Ambiguous occurrences need better alignment; never choose one by proximity alone.
        if len(matches) != 1:
            continue
        match = matches[0]
        if not re.search(r"[-–—]", match[0]) and len(match[0].split()) == len(form.parts):
            # Ordinary word spacing may be deliberate, even if the page also uses a compound.
            continue
        expected = form.parts[0] + "".join(
            separator + part
            for separator, part in zip(form.separators, form.parts[1:], strict=True)
        )
        if match[0] != expected:
            text = text[: match.start()] + expected + text[match.end() :]
            repaired += 1
    return text, repaired
