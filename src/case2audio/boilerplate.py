"""Remove publishing notices from narration while retaining adjacent case prose."""

from __future__ import annotations

import re

_HEADING = re.compile(
    r"^(?:#{1,6}\s*)?(?:copyright information|copyright notice|copyright and permissions|"
    r"rights and permissions)\s*[:.]?$",
    re.I,
)
_COPYRIGHT_START = re.compile(
    r"^(?:copyright\s*:?[ \t]*(?:©|\(c\))?|©|\(c\))\s*\d{4}\b",
    re.I,
)
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
_NOTICE_SENTENCES = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r"^all rights reserved[.!]?\s*",
        r"^this (?:case|document|article) includes (?:minor editorial changes|changes made|"
        r"editorial changes)[^.]*\.(?:\s*|$)",
        r"^this (?:case|document) is for teaching purposes only[^.]*\.(?:\s*|$)",
        r"^this (?:case|document|article) cannot be (?:used|reproduced)[^.]*"
        r"without (?:explicit |written )?permission[^.]*\.(?:\s*|$)",
        # Match the email boundary even when the next real sentence follows without a period.
        r"^to obtain permission,? please (?:visit|contact) .+?"
        r"e-?mail\s+[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?:\s*\.)?\s*",
        r"^to obtain permission,? please visit\s+\S+?(?:\.(?=\s+[A-Z])|$)\s*",
    )
)


def strip_publishing_boilerplate(markdown: str) -> str:
    """Only strip recognized notice prefixes, never everything under a copyright heading."""

    kept: list[str] = []
    for paragraph in re.split(r"\n\s*\n", markdown):
        original = paragraph
        flat = re.sub(r"\s+", " ", paragraph).strip()
        normalized = flat
        # Remove cover metadata before paragraph joining can attach the next page's prose.
        if re.fullmatch(r"ID\s*#\s*\S+", flat, re.I):
            continue
        flat = re.sub(
            r"^PUBLISHED ON\s+(?:[A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2})\b\s*",
            "",
            flat,
            flags=re.I,
        )
        if _HEADING.fullmatch(flat):
            continue
        if _COPYRIGHT_START.match(flat):
            # Stop at the first sentence, so an adjacent factual sentence survives.
            end = _SENTENCE_END.search(flat)
            flat = flat[end.end() :].lstrip() if end else ""

        while flat:
            for pattern in _NOTICE_SENTENCES:
                updated, count = pattern.subn("", flat, count=1)
                if count:
                    flat = updated.lstrip()
                    break
            else:
                break
        if flat:
            # Preserve Markdown line structure when this paragraph contained no notice.
            kept.append(original if flat == normalized else flat)
    return "\n\n".join(kept)
