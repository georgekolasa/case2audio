"""Local PDF extraction through Docling."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .cleaner import CleanerOptions, clean_markdown
from .errors import Case2AudioError
from .reading_order import TextBlock, order_for_narration, render_markdown


@dataclass(frozen=True)
class ExtractionResult:
    """Both forms make cleanup problems easy to inspect later."""

    narration: str
    markdown: str
    narration_markdown: str
    document_json: dict[str, object]


def extract_pdf(
    pdf_path: Path,
    *,
    use_ocr: bool = True,
    table_mode: str = "skip",
    extra_drop_patterns: tuple[str, ...] = (),
) -> ExtractionResult:
    """Extract one local PDF and omit Docling's furniture layer."""

    path = pdf_path.expanduser().resolve()
    if not path.is_file():
        raise Case2AudioError(f"PDF not found: {path}")
    if path.suffix.lower() != ".pdf":
        raise Case2AudioError(f"Expected a .pdf file: {path}")

    # Lazy imports keep `case2audio --help` fast despite Docling's large ML stack.
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
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
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
    # Docling's generic order is visual; our thin layer prioritizes uninterrupted listening.
    narration_markdown = _build_narration_markdown(document, ContentLayer.BODY)
    # Linearized tables require Docling's table serializer; the default skips them entirely.
    cleanup_source = narration_markdown if table_mode == "skip" else markdown
    narration = clean_markdown(
        cleanup_source,
        CleanerOptions(table_mode=table_mode, extra_drop_patterns=extra_drop_patterns),
    )
    return ExtractionResult(
        narration=narration,
        markdown=markdown,
        narration_markdown=narration_markdown,
        document_json=document.export_to_dict(),
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


def _build_narration_markdown(document, body_layer) -> str:
    """Map Docling items into the small geometry model used by reading-order policy."""

    blocks: list[TextBlock] = []
    for item in document.texts:
        if item.content_layer != body_layer or not item.prov:
            continue
        # Header/footer labels are skipped even if a parser accidentally marks them BODY.
        label = item.label.value
        if label in {"page_header", "page_footer"}:
            continue

        provenance = item.prov[0]
        page_size = document.pages[provenance.page_no].size
        blocks.append(
            TextBlock(
                text=item.text,
                label=label,
                page=provenance.page_no,
                left=provenance.bbox.l,
                top=provenance.bbox.t,
                right=provenance.bbox.r,
                bottom=provenance.bbox.b,
                page_width=page_size.width,
                page_height=page_size.height,
            )
        )

    main, sidebars = order_for_narration(blocks)
    return render_markdown(main, sidebars)
