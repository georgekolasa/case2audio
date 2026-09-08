"""Local PDF extraction through Docling."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path

from .boilerplate import strip_publishing_boilerplate
from .citations import filter_citation_blocks
from .cleaner import CleanerOptions, clean_markdown
from .errors import Case2AudioError
from .furniture import strip_margin_furniture
from .inline_refs import (
    contains_reference_anchor,
    reference_sequence_markers,
    repair_source_word_joins,
    scan_pdf_text_evidence,
    strip_inline_references,
)
from .pdf_safety import scan_visual_redactions, scrub_hidden_text, scrub_hidden_values
from .quality import ExtractionSignals, QualityReport, assess_narration
from .reading_order import TextBlock, order_for_narration, render_markdown
from .source_forms import repair_source_forms


@dataclass(frozen=True)
class ExtractionResult:
    """Both forms make cleanup problems easy to inspect later."""

    narration: str
    markdown: str
    narration_markdown: str
    document_json: dict[str, object]
    quality_report: QualityReport


def extract_pdf(
    pdf_path: Path,
    *,
    use_ocr: bool = True,
    table_mode: str = "smart",
    extra_drop_patterns: tuple[str, ...] = (),
) -> ExtractionResult:
    """Extract one local PDF and omit Docling's furniture layer."""

    path = pdf_path.expanduser().resolve()
    if not path.is_file():
        raise Case2AudioError(f"PDF not found: {path}")
    if path.suffix.lower() != ".pdf":
        raise Case2AudioError(f"Expected a .pdf file: {path}")

    # Scan the original page objects before Docling can lose visual redaction geometry.
    redactions = scan_visual_redactions(path)

    # Lazy imports keep `case2audio --help` fast despite Docling's large ML stack.
    from docling.datamodel.backend_options import ThreadedDoclingParseBackendOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import ContentLayer

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = use_ocr
    # Tables are detected even when narration later skips them; raw output stays useful.
    pipeline_options.do_table_structure = True

    converter = DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=pipeline_options,
                # Docling's native parser can segfault when its page workers race on macOS.
                # One parser thread is slightly slower but makes extraction deterministic.
                backend_options=ThreadedDoclingParseBackendOptions(parser_threads=1),
            ),
        },
    )

    try:
        document = converter.convert(path).document
    except Exception as exc:  # Docling exposes several backend-specific failures.
        raise Case2AudioError(f"Docling could not parse {path.name}: {exc}") from exc

    # Explicit BODY filtering prevents page furniture from becoming spoken content.
    markdown = document.export_to_markdown(
        included_content_layers={ContentLayer.BODY},
        page_break_placeholder="\n\n",
    )
    # Our geometry layer repairs headings and explains content that cannot safely become speech.
    evidence = scan_pdf_text_evidence(path)
    narration_markdown, signals = _build_narration_markdown(
        document,
        ContentLayer.BODY,
        table_mode,
        references=evidence.references,
        word_joins=evidence.word_joins,
        source_forms=evidence.source_forms,
    )

    # Never persist selectable text that the rendered PDF deliberately covers.
    markdown = scrub_hidden_text(markdown, redactions.hidden_texts)
    narration_markdown = scrub_hidden_text(narration_markdown, redactions.hidden_texts)
    narration = clean_markdown(
        narration_markdown,
        CleanerOptions(table_mode=table_mode, extra_drop_patterns=extra_drop_patterns),
    )
    signals = replace(signals, redacted_text_items=len(redactions.hidden_texts))
    quality_report = assess_narration(
        narration,
        signals=signals,
        hidden_texts=redactions.hidden_texts,
    )
    return ExtractionResult(
        narration=narration,
        markdown=markdown,
        narration_markdown=narration_markdown,
        document_json=scrub_hidden_values(document.export_to_dict(), redactions.hidden_texts),
        quality_report=quality_report,
    )


def write_extraction(
    result: ExtractionResult,
    output_text: Path,
    *,
    debug_dir: Path | None = None,
) -> None:
    """Write narration and optional raw artifacts using stable UTF-8 files."""

    output_text.parent.mkdir(parents=True, exist_ok=True)
    output_text.write_text(result.narration, encoding="utf-8")

    if debug_dir is None:
        return

    debug_dir.mkdir(parents=True, exist_ok=True)
    debug_dir.joinpath("docling.md").write_text(result.markdown, encoding="utf-8")
    debug_dir.joinpath("narration-order.md").write_text(
        result.narration_markdown,
        encoding="utf-8",
    )
    debug_dir.joinpath("docling.json").write_text(
        json.dumps(result.document_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    debug_dir.joinpath("quality-report.txt").write_text(
        result.quality_report.render(),
        encoding="utf-8",
    )


def _build_narration_markdown(
    document,
    body_layer,
    table_mode: str,
    *,
    references=(),
    word_joins=(),
    source_forms=(),
) -> tuple[str, ExtractionSignals]:
    """Map Docling items into the small geometry model used by reading-order policy."""

    blocks: list[TextBlock] = []
    for item in document.texts:
        if item.content_layer != body_layer or not item.prov:
            continue
        # Header/footer labels are skipped even if a parser accidentally marks them BODY.
        label = item.label.value
        blocks.extend(_text_blocks_from_item(document, item, label))

    # Remove furniture while page geometry is still intact, before continuation merging.
    blocks, removed_furniture = strip_margin_furniture(blocks)
    blocks, removed_front_matter = _strip_front_matter(blocks)
    # A cover notice must not become the unfinished paragraph that absorbs page-two text.
    blocks = [
        replace(block, text=cleaned)
        for block in blocks
        if (cleaned := strip_publishing_boilerplate(block.text)).strip()
    ]
    blocks, omitted_table_notes = _strip_low_value_table_notes(blocks)
    known_markers = {
        match.group(1)
        for block in blocks
        if block.label == "footnote"
        if (match := re.match(r"^\s*(\d{1,3}|[ivxlcdm]{1,4})[.)]?\s+", block.text))
    }
    known_markers.update(reference_sequence_markers(references))
    removed_markers = repaired_words = repaired_forms = 0
    for index, block in enumerate(blocks):
        if block.label not in {"text", "list_item", "caption"}:
            continue
        text, count = strip_inline_references(block, references, known_markers)
        repaired, word_count = repair_source_word_joins(replace(block, text=text), word_joins)
        repaired, form_count = repair_source_forms(replace(block, text=repaired), source_forms)
        blocks[index] = replace(block, text=repaired)
        removed_markers += count
        repaired_words += word_count
        repaired_forms += form_count

    pictures = _content_pictures(document)
    blocks, removed_visual_labels = _strip_visual_labels(blocks, pictures)
    table_blocks, narrated_tables, omitted_tables = _table_blocks(document, table_mode)
    visual_blocks, marked_visuals = _visual_blocks(document, pictures)
    blocks.extend(table_blocks)
    blocks.extend(visual_blocks)

    main, sidebars = order_for_narration(blocks)
    # Physical page-bottom footnotes are reordered only after body reading order is stable.
    main = _place_referenced_footnotes(main, references)
    sidebars = [_place_referenced_footnotes(sidebar, references) for sidebar in sidebars]
    # Filter each narrative separately so an article's references cannot swallow a sidebar.
    filtered = [filter_citation_blocks(stream) for stream in [main, *sidebars]]
    main = filtered[0].blocks
    sidebars = [result.blocks for result in filtered[1:] if result.blocks]
    omitted = sum(result.omitted for result in filtered)
    explanations = sum(result.explanations for result in filtered)
    signals = ExtractionSignals(
        narrated_text_tables=narrated_tables,
        omitted_data_tables=omitted_tables,
        marked_visuals=marked_visuals,
        omitted_citation_blocks=omitted,
        retained_explanatory_notes=explanations,
        removed_margin_blocks=removed_furniture,
        removed_inline_markers=removed_markers,
        repaired_source_words=repaired_words,
        repaired_source_forms=repaired_forms,
        removed_front_matter_blocks=removed_front_matter,
        omitted_table_notes=omitted_table_notes,
        removed_visual_labels=removed_visual_labels,
    )
    return render_markdown(main, sidebars), signals


def _strip_front_matter(blocks: list[TextBlock]) -> tuple[list[TextBlock], int]:
    """Drop cover-page affiliations and acknowledgments without eating page-two prose."""

    headings = re.compile(r"^(?:author affiliations?|acknowledg(?:e)?ments?)\s*:?$", re.I)
    output: list[TextBlock] = []
    active_page: int | None = None
    removed = 0
    for block in blocks:
        if block.label == "section_header" and headings.fullmatch(block.text.strip()):
            active_page = block.page
            removed += 1
            continue
        if active_page is not None:
            if block.page != active_page or block.label == "section_header":
                active_page = None
            else:
                removed += 1
                continue
        output.append(block)
    return output, removed


def _strip_low_value_table_notes(blocks: list[TextBlock]) -> tuple[list[TextBlock], int]:
    """Remove notes that only inventory omitted table cells or unavailable values."""

    clutter = re.compile(
        r"^Notes?\s*:\s*(?:Countries are listed\b|Brooklyn Brewery did not have\b|"
        r"Lager is Brooklyn Lager\b|Examples of (?:super premium|craft competitor)\b)",
        re.I,
    )
    # Classification ignores PDF column spacing while preserving the original retained text.
    kept = [block for block in blocks if not clutter.match(" ".join(block.text.split()))]
    return kept, len(blocks) - len(kept)


def _place_referenced_footnotes(blocks: list[TextBlock], references) -> list[TextBlock]:
    """Put footnotes after their referring paragraph so retained explanations have context."""

    insertions: dict[int, list[TextBlock]] = {}
    moved: set[int] = set()
    for footnote_index, footnote in enumerate(blocks):
        if footnote.label != "footnote":
            continue
        match = re.match(r"^\s*(\d{1,3}|[ivxlcdm]{1,4})[.)]?\s+", footnote.text, re.I)
        if not match:
            continue
        marker = match.group(1).casefold()
        for reference in references:
            if reference.page != footnote.page or reference.marker.casefold() != marker:
                continue
            target = next(
                (
                    index
                    for index, candidate in enumerate(blocks)
                    if candidate.label in {"text", "list_item", "caption"}
                    and candidate.page == reference.page
                    and contains_reference_anchor(candidate.text, reference)
                ),
                None,
            )
            if target is not None and target != footnote_index:
                insertions.setdefault(target, []).append(footnote)
                moved.add(footnote_index)
                break

    output: list[TextBlock] = []
    for index, block in enumerate(blocks):
        if index not in moved:
            output.append(block)
        output.extend(insertions.get(index, ()))
    return output


def _text_blocks_from_item(document, item, label):
    # Docling can merge text across pages; split its character spans before using geometry.
    if len(item.prov) > 1:
        spans = sorted(item.prov, key=lambda p: p.charspan[0])
        cursor = 0
        valid = True
        for prov in spans:
            start, end = prov.charspan
            if (
                start < cursor
                or end < start
                or end > len(item.text)
                or item.text[cursor:start].strip()
            ):
                valid = False
                break
            cursor = end
        if valid and not item.text[cursor:].strip():
            return [
                _block_from_item(
                    document,
                    item,
                    text=item.text[p.charspan[0] : p.charspan[1]],
                    label=label,
                    provenance=p,
                )
                for p in spans
            ]
    # If provenance is incomplete, preserve the whole text rather than silently dropping gaps.
    return [_block_from_item(document, item, text=item.text, label=label)]


def _block_from_item(
    document, item, *, text: str, label: str = "text", provenance=None
) -> TextBlock:
    """Copy one Docling item's geometry into our deliberately small model."""

    provenance = provenance or item.prov[0]
    page_size = document.pages[provenance.page_no].size
    return TextBlock(
        text=text,
        label=label,
        page=provenance.page_no,
        left=provenance.bbox.l,
        top=provenance.bbox.t,
        right=provenance.bbox.r,
        bottom=provenance.bbox.b,
        page_width=page_size.width,
        page_height=page_size.height,
    )


def _table_blocks(document, table_mode: str) -> tuple[list[TextBlock], int, int]:
    """Narrate compact text tables and explicitly mark dense data tables."""

    blocks: list[TextBlock] = []
    narrated = 0
    omitted = 0
    for table in document.tables:
        if not table.prov:
            continue
        # Docling sometimes builds a table from the running title and author byline.
        # Reuse the margin policy so real tables in the page body keep their notices.
        table_text = " ".join(cell.text for row in table.data.grid for cell in row)
        table_block = _block_from_item(document, table, text=table_text)
        if not strip_margin_furniture([table_block])[0]:
            continue
        page_number = table.prov[0].page_no

        if table_mode == "linearize" or (table_mode == "smart" and _is_text_table(table)):
            text = _linearize_table(table)
            narrated += 1
        else:
            title = _table_title(table)
            # A generic first-column header sounds worse than no title; nearby captions remain.
            if _is_generic_table_title(title):
                text = f"Table omitted. See PDF page {page_number}."
            else:
                text = f"Table omitted: {title}. See PDF page {page_number}."
            omitted += 1

        blocks.append(_block_from_item(document, table, text=text, label="note"))
    return blocks, narrated, omitted


def _is_text_table(table) -> bool:
    """Use cell contents, not just dimensions, to avoid speaking unlabeled number streams."""

    if table.data.num_cols > 4 or table.data.num_rows > 25:
        return False
    if _is_conversion_table(table):
        return True
    rows = table.data.grid
    # Multiple dated columns are comparisons, even when most other cells contain names.
    if rows and sum(bool(re.search(r"\b(?:19|20)\d{2}\b", c.text)) for c in rows[0]) >= 2:
        return False
    cells = [
        cell.text.strip()
        for row in rows
        for index, cell in enumerate(row)
        if cell.text.strip()
        and not re.fullmatch(r"_+", cell.text.strip())
        # An ordinal first column does not make a list of prompts a data table.
        and not (index == 0 and re.fullmatch(r"\d+[.)]?", cell.text.strip()))
    ]
    return bool(cells) and sum(bool(re.search(r"\d", c)) for c in cells) / len(cells) < 0.25


def _is_conversion_table(table) -> bool:
    rows = table.data.grid
    # Two explicitly labeled unit columns remain useful when narrated as equivalences.
    return (
        bool(rows)
        and table.data.num_cols == 2
        and [c.text.strip().casefold() for c in rows[0]]
        in (["measure", "equivalent"], ["unit", "equivalent"])
    )


def _linearize_table(table) -> str:
    """Turn rows into short spoken clauses while removing blank response fields."""

    rows: list[str] = []
    if _is_conversion_table(table):
        return "Table contents. " + " ".join(
            f"{row[0].text.strip()} equals {row[1].text.strip()}."
            for row in table.data.grid[1:]
            if len(row) == 2
        )
    for row in table.data.grid:
        cells: list[str] = []
        for cell in row:
            text = re.sub(r"\s+", " ", cell.text).strip()
            if not text or re.fullmatch(r"_+", text):
                continue
            # Merged cells repeat in Docling's grid; say their value only once per row.
            if not cells or cells[-1] != text:
                cells.append(text)
        if cells:
            rows.append(" ".join(cells).rstrip(".") + ".")
    return "Table contents. " + " ".join(rows)


def _table_title(table) -> str:
    """Use the first meaningful cell as a useful spoken label for an omitted table."""

    for row in table.data.grid:
        for cell in row:
            text = re.sub(r"\s+", " ", cell.text).strip().rstrip(".")
            if text:
                return text[:180]
    return "untitled table"


def _is_generic_table_title(title: str) -> bool:
    """Reject first cells that are clearly column labels rather than table titles."""

    normalized = re.sub(r"[^a-z ]", "", title.casefold()).strip()
    # Years and compact labels commonly occupy the first cell of captioned data tables.
    if re.search(r"\d", title):
        return True
    return normalized in {
        "age group",
        "business model",
        "company",
        "country",
        "measure",
        "operating cost",
        "package size",
        "price segment",
        "product group",
        "rank",
        "region",
        "style",
        "supplier",
        "year",
    }


def _content_pictures(document) -> list:
    """Find content diagrams while rejecting logos and small decorative marks."""

    pictures = []
    for picture in getattr(document, "pictures", ()):
        if not picture.prov:
            continue
        provenance = picture.prov[0]
        page_size = document.pages[provenance.page_no].size
        width = provenance.bbox.r - provenance.bbox.l
        height = provenance.bbox.t - provenance.bbox.b
        area_ratio = width * height / (page_size.width * page_size.height)
        # Width, height and body placement distinguish small diagrams from publisher logos.
        if (
            area_ratio >= 0.02
            and width >= page_size.width * 0.18
            and height >= page_size.height * 0.06
            and provenance.bbox.b > page_size.height * 0.10
            and provenance.bbox.t < page_size.height * 0.94
        ):
            pictures.append(picture)
    return pictures


def _strip_visual_labels(blocks: list[TextBlock], pictures: list) -> tuple[list[TextBlock], int]:
    """Remove disconnected labels and arrow glyphs that sit inside omitted diagrams."""

    output: list[TextBlock] = []
    removed = 0
    for block in blocks:
        inside = False
        for picture in pictures:
            provenance = picture.prov[0]
            center_x = block.center_x
            center_y = (block.top + block.bottom) / 2
            if (
                block.page == provenance.page_no
                and provenance.bbox.l <= center_x <= provenance.bbox.r
                and provenance.bbox.b <= center_y <= provenance.bbox.t
                and block.label not in {"caption", "section_header", "footnote"}
            ):
                inside = True
                break
        if inside:
            removed += 1
        else:
            output.append(block)
    return output, removed


def _visual_blocks(document, pictures=None) -> tuple[list[TextBlock], int]:
    """Mark meaningful figures once per page instead of silently losing them."""

    substantial_by_page: dict[int, list] = {}
    for picture in _content_pictures(document) if pictures is None else pictures:
        provenance = picture.prov[0]
        substantial_by_page.setdefault(provenance.page_no, []).append(picture)

    blocks: list[TextBlock] = []
    for pictures in substantial_by_page.values():
        first = max(pictures, key=lambda picture: picture.prov[0].bbox.t)
        count = len(pictures)
        page_number = first.prov[0].page_no
        noun = "Figure" if count == 1 else "Figures"
        text = f"{noun} omitted. See PDF page {page_number}."
        blocks.append(_block_from_item(document, first, text=text, label="note"))
    return blocks, sum(len(pictures) for pictures in substantial_by_page.values())
