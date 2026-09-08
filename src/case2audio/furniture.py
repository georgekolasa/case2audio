"""Remove repeated margin text before it can merge into real paragraphs."""

from __future__ import annotations

import re
from collections import defaultdict

from .reading_order import TextBlock


def _key(text: str) -> str:
    # Spacing, footnote symbols and changing page numbers do not change a running title.
    return re.sub(r"[\W\d_]+", "", text.casefold())


def strip_margin_furniture(blocks: list[TextBlock]) -> tuple[list[TextBlock], int]:
    pages: dict[str, set[int]] = defaultdict(set)

    def in_margin(block: TextBlock) -> bool:
        # A whole block must be in the margin; never crop the bottom of a body paragraph.
        return block.top < block.page_height * 0.11 or block.bottom > block.page_height * 0.94

    for block in blocks:
        if in_margin(block) and block.label not in {"footnote", "note"}:
            pages[_key(block.text)].add(block.page)

    known = [
        _key(block.text)
        for block in blocks
        if in_margin(block)
        and (
            re.search(r"\bpage\s+\d+\b", block.text, re.I) or re.match(r"^by\s+", block.text, re.I)
        )
    ]

    output = []
    for block in blocks:
        text = block.text.strip()
        margin = in_margin(block) and block.label not in {"footnote", "note"}
        page_label = re.search(r"\bpage\s+\d+\s*\||\|\s*page\s+\d+\b", text, re.I)
        byline = re.match(r"^by\s+[A-Z]", text, re.I)
        # Repetition catches split title/byline fragments without hard-coding publisher names.
        repeated = len(pages[_key(text)]) >= 2 and len(_key(text)) >= 4
        # A footer can be split into a one-off title fragment or the last author's surname.
        fragment = (len(_key(text)) >= 12 or bool(re.search(r"[†‡]$", text))) and any(
            _key(text) in key for key in known if key != _key(text)
        )
        if block.label in {"page_header", "page_footer"}:
            continue
        if margin and (page_label or byline or repeated or fragment):
            continue
        output.append(block)
    return output, len(blocks) - len(output)
