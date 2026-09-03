"""Local PDF extraction through Docling."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .cleaner import CleanerOptions, clean_markdown
from .errors import Case2AudioError
from .pdf_safety import scan_visual_redactions, scrub_hidden_text, scrub_hidden_values
from .quality import ExtractionSignals, QualityReport, assess_narration
from .reading_order import TextBlock, order_for_narration, render_markdown


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
    narration_markdown, signals = _build_narration_markdown(
        document,
        ContentLayer.BODY,
        table_mode,
    )

    # Never persist selectable text that the rendered PDF deliberately covers.
    markdown = scrub_hidden_text(markdown, redactions.hidden_texts)
    narration_markdown = scrub_hidden_text(narration_markdown, redactions.hidden_texts)
    narration = clean_markdown(
        narration_markdown,
        CleanerOptions(table_mode=table_mode, extra_drop_patterns=extra_drop_patterns),
    )
    signals = ExtractionSignals(
        redacted_text_items=len(redactions.hidden_texts),
        narrated_text_tables=signals.narrated_text_tables,
        omitted_data_tables=signals.omitted_data_tables,
        marked_visuals=signals.marked_visuals,
    )
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
) -> tuple[str, ExtractionSignals]:
    """Map Docling items into the small geometry model used by reading-order policy."""

    blocks: list[TextBlock] = []
    for item in document.texts:
        if item.content_layer != body_layer or not item.prov:
            continue
        # Header/footer labels are skipped even if a parser accidentally marks them BODY.
        label = item.label.value
        if label in {"page_header", "page_footer"}:
            continue

        blocks.append(_block_from_item(document, item, text=item.text, label=label))

    table_blocks, narrated_tables, omitted_tables = _table_blocks(document, table_mode)
    visual_blocks, marked_visuals = _visual_blocks(document)
    blocks.extend(table_blocks)
    blocks.extend(visual_blocks)

    main, sidebars = order_for_narration(blocks)
    signals = ExtractionSignals(
        narrated_text_tables=narrated_tables,
        omitted_data_tables=omitted_tables,
        marked_visuals=marked_visuals,
    )
    return render_markdown(main, sidebars), signals


def _block_from_item(document, item, *, text: str, label: str = "text") -> TextBlock:
    """Copy one Docling item's geometry into our deliberately small model."""

    provenance = item.prov[0]
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
        page_number = table.prov[0].page_no

        if table_mode == "linearize" or (table_mode == "smart" and _is_text_table(table)):
            text = _linearize_table(table)
            narrated += 1
        else:
            title = _table_title(table)
            description = f"{table.data.num_rows} rows and {table.data.num_cols} columns"
            text = (
                f"Data table omitted from narration: {title}. It contains {description}. "
                f"Review source PDF page {page_number} for the values."
            )
            omitted += 1

        blocks.append(_block_from_item(document, table, text=text, label="note"))
    return blocks, narrated, omitted


def _is_text_table(table) -> bool:
    """Small narrow tables usually contain prompts or lists rather than dense measurements."""

    return table.data.num_cols <= 4 and table.data.num_rows <= 25


def _linearize_table(table) -> str:
    """Turn rows into short spoken clauses while removing blank response fields."""

    rows: list[str] = []
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


def _visual_blocks(document) -> tuple[list[TextBlock], int]:
    """Mark substantial figures once per page instead of silently losing them."""

    substantial_by_page: dict[int, list] = {}
    for picture in document.pictures:
        if not picture.prov:
            continue
        provenance = picture.prov[0]
        page_size = document.pages[provenance.page_no].size
        area = (provenance.bbox.r - provenance.bbox.l) * (
            provenance.bbox.t - provenance.bbox.b
        )
        # Small logos and decorative marks add no value to an audiobook.
        if area < page_size.width * page_size.height * 0.08:
            continue
        substantial_by_page.setdefault(provenance.page_no, []).append(picture)

    blocks: list[TextBlock] = []
    for pictures in substantial_by_page.values():
        first = max(pictures, key=lambda picture: picture.prov[0].bbox.t)
        count = len(pictures)
        page_number = first.prov[0].page_no
        noun = "figure" if count == 1 else "figures"
        text = (
            f"Visual exhibit notice. {count} substantial {noun} on this page cannot be "
            f"reliably converted to speech. Review source PDF page {page_number} for layout "
            "and relationships."
        )
        blocks.append(_block_from_item(document, first, text=text, label="note"))
    return blocks, sum(len(pictures) for pictures in substantial_by_page.values())
