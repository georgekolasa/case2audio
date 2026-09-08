from types import SimpleNamespace

import pytest

from case2audio.citations import filter_citation_blocks
from case2audio.reading_order import TextBlock


def block(text, label="text", page=1):
    return TextBlock(text, label, page, 50, 600, 550, 500, 612, 792)


@pytest.mark.parametrize("heading", ["References", "Bibliography", "Works Cited", "Endnotes"])
def test_reference_lists_stop_at_the_next_section(heading):
    result = filter_citation_blocks(
        [
            block("Case narrative."),
            block(heading, "section_header"),
            block('1 Smith, "Some study," 2020.', "footnote"),
            block("Continuation of that source on a new page.", page=2),
            block("Appendix", "section_header", page=2),
            block("Useful appendix details.", page=2),
        ]
    )
    assert [item.text for item in result.blocks] == [
        "Case narrative.",
        "Appendix",
        "Useful appendix details.",
    ]
    assert result.omitted == 3


def test_explanatory_footnotes_and_numbers_survive():
    result = filter_citation_blocks(
        [
            block("Revenue was 2024 million, up 20%. See Exhibit 3."),
            block("i This model assumes two firms. However, pricing can vary.", "footnote"),
            block("2 Sales grew in 2024 because demand increased.", "footnote"),
            block('3 Smith, "Revenue Trends," 2024.', "footnote"),
        ]
    )
    assert [item.text for item in result.blocks] == [
        "Revenue was 2024 million, up 20%. See Exhibit 3.",
        "This model assumes two firms. However, pricing can vary.",
        "Sales grew in 2024 because demand increased.",
    ]
    assert result.explanations == 2


def test_mixed_endnote_preserves_explanation_without_source_details():
    result = filter_citation_blocks(
        [
            block("Endnotes", "section_header"),
            block(
                '1 Smith, "Benchmarks," 2024. Note: Sample sizes differ between firms.', "footnote"
            ),
            block('2 Jones, "Costs," 2023. This ratio excludes freight.', "footnote"),
        ]
    )
    assert [item.text for item in result.blocks] == [
        "Explanatory notes",
        "Sample sizes differ between firms.",
        "This ratio excludes freight.",
    ]
    assert result.explanations == 2


def test_generic_notes_section_is_not_treated_as_bibliography():
    blocks = [
        block("Notes", "section_header"),
        block("One barrel equals 31 US gallons."),
        block("Figures exclude taxes and shipping."),
    ]
    assert filter_citation_blocks(blocks).blocks == blocks


def test_generic_notes_with_multiple_citations_are_removed():
    result = filter_citation_blocks(
        [
            block("Notes", "section_header"),
            block('1 Smith, "Costs," 2024.'),
            block('2 Jones, "Revenue," 2023.'),
        ]
    )
    assert result.blocks == []


def test_source_label_continuations_do_not_swallow_following_prose():
    result = filter_citation_blocks(
        [
            block("Source:"),
            block('Smith, "Costs," 2024.'),
            block("https://example.test/source"),
            block("The company then expanded."),
            block("Source: Company documents. Note: Figures are in millions."),
        ]
    )
    assert [item.text for item in result.blocks] == [
        "The company then expanded.",
        "Figures are in millions.",
    ]


def test_book_recommendation_is_citation_only():
    result = filter_citation_blocks(
        [
            block(
                "ii For a popular book on strategy, see Smith, The Advantage (Publisher, 2024).",
                "footnote",
            ),
        ]
    )
    assert result.blocks == []


def test_complete_source_line_does_not_consume_dated_body_paragraph():
    result = filter_citation_blocks(
        [
            block("Source: Company documents."),
            block("Revenue increased, reaching record levels in 2024."),
        ]
    )
    assert [item.text for item in result.blocks] == [
        "Revenue increased, reaching record levels in 2024.",
    ]


def test_extraction_opt_out_keeps_citations(monkeypatch):
    from case2audio import extractor

    blocks = [
        block("Main narrative."),
        block("Endnotes", "section_header"),
        block('1 Smith, "Costs," 2024.', "footnote"),
    ]
    document = SimpleNamespace(
        texts=[
            SimpleNamespace(
                content_layer="body",
                prov=[True],
                label=SimpleNamespace(value=b.label),
                text=b.text,
                block=b,
            )
            for b in blocks
        ]
    )
    monkeypatch.setattr(extractor, "_block_from_item", lambda doc, item, **_: item.block)
    monkeypatch.setattr(extractor, "_table_blocks", lambda *args: ([], 0, 0))
    monkeypatch.setattr(extractor, "_visual_blocks", lambda *args: ([], 0))
    monkeypatch.setattr(extractor, "order_for_narration", lambda blocks: (blocks, []))
    filtered, signals = extractor._build_narration_markdown(document, "body", "smart")
    full, _ = extractor._build_narration_markdown(document, "body", "smart", keep_citations=True)
    assert "Smith" not in filtered
    assert "Smith" in full
    assert signals.omitted_citation_blocks == 2


def test_continued_reference_heading_does_not_resume_narrative():
    result = filter_citation_blocks(
        [
            block("References", "section_header"),
            block('Smith, "Costs," 2024.'),
            block("References (continued)", "section_header", page=2),
            block('Jones, "Revenue," 2023.', page=2),
        ]
    )
    assert result.blocks == []
