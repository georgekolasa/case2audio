from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from case2audio import extractor
from case2audio.ocr_recovery import polish_ocr_narration
from case2audio.quality import ExtractionSignals


@pytest.mark.parametrize("recovered", ["Readable case prose.", "161 162 163 i255 " * 100])
def test_bad_text_retries_once_and_bad_retry_stays_blocked(tmp_path, monkeypatch, recovered):
    from docling import document_converter
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import OcrMode

    path = tmp_path / "case.pdf"
    path.touch()
    monkeypatch.setattr(
        extractor, "scan_visual_redactions", lambda _: SimpleNamespace(hidden_texts=())
    )
    monkeypatch.setattr(
        extractor,
        "scan_pdf_text_evidence",
        Mock(
            side_effect=AssertionError(
                "Corrupt embedded text must not be used as OCR repair evidence"
            )
        ),
    )
    from case2audio import ocr_recovery

    monkeypatch.setattr(ocr_recovery, "recover_currency_suffixes", lambda *_: 0)

    def document(text):
        doc = Mock()
        doc.export_to_markdown.return_value = text
        doc.export_to_dict.return_value = {}
        return doc

    converter = Mock()
    converter.convert.side_effect = [
        SimpleNamespace(document=document("161 162 163 i255 " * 100)),
        SimpleNamespace(document=document(recovered)),
    ]
    factory = Mock(return_value=converter)
    monkeypatch.setattr(document_converter, "DocumentConverter", factory)
    monkeypatch.setattr(
        extractor,
        "_build_narration_markdown",
        lambda doc, *_args, **_kwargs: (doc.export_to_markdown(), ExtractionSignals()),
    )
    result = extractor.extract_pdf(path, use_ocr=False)
    assert converter.convert.call_count == 2
    options = [
        call.kwargs["format_options"][InputFormat.PDF].pipeline_options
        for call in factory.call_args_list
    ]
    assert options[0].do_ocr is False
    assert options[1].do_ocr is True
    assert options[1].ocr_options.mode == OcrMode.FULL_PAGE
    assert bool(result.quality_report.blocking) == ("i255" in recovered)
    assert "OCR_RECOVERY" in result.quality_report.render()


@pytest.mark.parametrize(
    "recognized,confidence,expected",
    [
        ("M.", 1.0, "Budget €29.5 M."),
        ("M.", 0.5, "Budget €29.5"),
        ("Maybe", 1.0, "Budget €29.5"),
    ],
)
def test_currency_suffix_requires_explicit_high_confidence_image_evidence(
    monkeypatch, recognized, confidence, expected
):
    import pypdfium2
    from ocrmac import ocrmac

    from case2audio.ocr_recovery import recover_currency_suffixes

    page = Mock()
    page.get_height.return_value = 792
    page.get_width.return_value = 612
    pdf = Mock()
    pdf.__enter__ = Mock(return_value=[page])
    pdf.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(pypdfium2, "PdfDocument", Mock(return_value=pdf))
    monkeypatch.setattr(
        ocrmac,
        "OCR",
        Mock(return_value=SimpleNamespace(recognize=lambda: [(recognized, confidence, ())])),
    )
    item = SimpleNamespace(
        text="Budget €29.5", prov=[SimpleNamespace(page_no=1, bbox=SimpleNamespace(l=80, b=450))]
    )
    recover_currency_suffixes(SimpleNamespace(texts=[item]), "unused.pdf")
    assert item.text == expected


def test_ocr_polish_repairs_verified_narration_forms():
    text = (
        "CCR evaluates projects. " * 15
        + "A 40year veteran discussed CR's chocolate business near Champs - Elysées. "
        + "It cost € 29.5 M. and sold for €55M. in year 10-therefore it mattered.\n\n"
        + "This is a long paragraph that ends without punctuation because OCR dropped it " * 2
    ).strip()
    polished = polish_ocr_narration(text)
    assert "40-year" in polished
    assert "CCR's chocolate business" in polished
    assert "Champs-Élysées" in polished
    assert "€29.5 million" in polished
    assert "€55 million" in polished
    assert "year 10 - therefore" in polished
    assert polished.endswith("it.")


def test_ocr_polish_does_not_guess_from_weak_acronym_evidence():
    text = "CCR appears twice. CCR is not dominant. CR's separate meaning stays."
    assert "CR's separate" in polish_ocr_narration(text)
