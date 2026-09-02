"""Small spatial heuristics for narration-friendly multi-column reading order."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class TextBlock:
    """The tiny subset of Docling geometry needed by narration policy."""

    text: str
    label: str
    page: int
    left: float
    top: float
    right: float
    bottom: float
    page_width: float
    page_height: float

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2


def order_for_narration(blocks: list[TextBlock]) -> tuple[list[TextBlock], list[list[TextBlock]]]:
    """Keep main prose continuous and move likely sidebar boxes to the end."""

    main: list[TextBlock] = []
    sidebars: list[list[TextBlock]] = []

    pages = sorted({block.page for block in blocks})
    for page in pages:
        page_blocks = [block for block in blocks if block.page == page]
        page_main, page_sidebars = _split_page(page_blocks)
        main.extend(page_main)
        sidebars.extend(page_sidebars)

    return _merge_continuations(main), [_merge_continuations(sidebar) for sidebar in sidebars]


def render_markdown(main: list[TextBlock], sidebars: list[list[TextBlock]]) -> str:
    """Serialize enough Markdown for headings and lists to survive cleanup."""

    sections = [_render_blocks(main)]
    if sidebars:
        # One explicit heading tells a listener why the article's boxes arrive later.
        sections.append("# Sidebars")
        sections.extend(_render_blocks(sidebar) for sidebar in sidebars)
    return "\n\n".join(section for section in sections if section.strip())


def _split_page(blocks: list[TextBlock]) -> tuple[list[TextBlock], list[list[TextBlock]]]:
    main: list[TextBlock] = []
    sidebars: list[list[TextBlock]] = []
    index = 0

    while index < len(blocks):
        block = blocks[index]
        if not _looks_like_sidebar_header(block):
            main.append(block)
            index += 1
            continue

        column_is_left = block.center_x < block.page_width / 2
        sidebar = [block]
        index += 1
        while index < len(blocks):
            candidate = blocks[index]
            candidate_is_left = candidate.center_x < candidate.page_width / 2
            # A column switch is Docling's strongest available signal that the box ended.
            if candidate_is_left != column_is_left:
                break
            sidebar.append(candidate)
            index += 1

        _recover_embedded_column_continuation(main, sidebar)
        sidebars.append(sidebar)

    # Items after a sidebar normally belong to the other main-text column.
    main.extend(blocks[index:])
    return main, sidebars


def _looks_like_sidebar_header(block: TextBlock) -> bool:
    letters = "".join(character for character in block.text if character.isalpha())
    word_count = len(re.findall(r"[A-Za-z]+", block.text))
    return (
        block.label == "section_header"
        and word_count >= 3
        and letters.isupper()
        # Cover titles and page-top article headings are not sidebars.
        and block.top < block.page_height * 0.75
        # Wide all-caps section breaks should remain in the main flow.
        and (block.right - block.left) < block.page_width * 0.48
    )


def _recover_embedded_column_continuation(main: list[TextBlock], sidebar: list[TextBlock]) -> None:
    """Undo a rare Docling merge where right-column prose lands in a sidebar citation."""

    if not main or not sidebar or not main[-1].text.rstrip().endswith("-"):
        return

    prefix, separator, suffix = sidebar[-1].text.rpartition(" - ")
    if not separator or not re.match(r"^[a-z]", suffix):
        return

    # The prior main fragment already owns the hyphen, as in "up-and-" + "comers".
    main[-1] = replace(main[-1], text=main[-1].text.rstrip() + suffix)
    sidebar[-1] = replace(sidebar[-1], text=prefix.rstrip())


def _merge_continuations(blocks: list[TextBlock]) -> list[TextBlock]:
    merged: list[TextBlock] = []
    for block in blocks:
        # Repeated running headers are visual navigation, not narration.
        if re.search(r"\(continued\)\s*$", block.text, re.IGNORECASE):
            continue
        if merged and _should_merge(merged[-1], block):
            merged[-1] = replace(
                merged[-1], text=f"{merged[-1].text.rstrip()} {block.text.lstrip()}"
            )
        else:
            merged.append(block)
    return merged


def _should_merge(previous: TextBlock, current: TextBlock) -> bool:
    if previous.label != "text" or current.label != "text":
        return False
    if re.search(r"[.!?][\"')\]]?\s*$", previous.text):
        return False
    # Lowercase starts strongly indicate a sentence continued across a column or page.
    return re.match(r"^[a-z]", current.text.lstrip()) is not None


def _render_blocks(blocks: list[TextBlock]) -> str:
    rendered: list[str] = []
    for block in blocks:
        text = block.text.strip()
        if not text:
            continue
        if block.label == "section_header":
            text = f"# {text}"
        elif block.label == "list_item":
            text = f"- {text}"
        rendered.append(text)
    return "\n\n".join(rendered)
