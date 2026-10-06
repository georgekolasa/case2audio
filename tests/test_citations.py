from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from case2audio.citations import filter_citation_blocks, strip_parenthetical_citations
from case2audio.reading_order import TextBlock

# Synthetic references prove author/year matches without relying on any licensed PDF.
AUTHOR_YEAR_REFERENCES = """## References
- Smith, A. (2009). A study.
- Smith, A. (2012). Another study.
- Smith, A. (2014). A study.
- Smith, A. (2015). A study.
- Smith, A. (2015a). A study.
- Immigration Policy Center. (2014). A report.
- PBS. (2014). A report.
- Policy Research Center. (2014). A report.
- Ricci v. DeStefano, 129 S. Ct. 2658 (2009).
"""


def block(text, label="text", page=1):
    return TextBlock(text, label, page, 50, 600, 550, 500, 612, 792)


@pytest.mark.parametrize(
    "citation",
    [
        "Smith, 2009",
        "Smith & Jones, 2014",
        "Smith, Brown, & Jones, 2015",
        "Benet-Martínez, Lee, & Leu, 2006",
        "Jansen, Otten, & van der Zee, 2015",
        "Smith et\u00a0 al., 2012",
        "Immigration Policy Center, 2014",
        "PBS, 2014",
        "Smith, 2015a, 2015b",
        "Smith, 2012, pp. 12–15",
        "see Smith, 2015",
        "Ricci v. DeStefano, 2009",
        "Smith & Jones,\n\n2001",
    ],
)
def test_author_year_citations_are_removed_without_damaging_the_sentence(citation):
    text, count = strip_parenthetical_citations(
        f"Teams perform better ({citation}), on average.",
        reference_markdown=AUTHOR_YEAR_REFERENCES,
    )
    assert text == "Teams perform better, on average."
    assert count == 1


def test_multiple_and_mixed_citations_keep_explanations_and_dashes():
    text, count = strip_parenthetical_citations(
        "Trust improves (Smith, 2014; Jones & Brown,\n\n2001)-a useful outcome. "
        "Pay rises (relative to the national average; Policy Research Center, 2014). "
        "Groups benefit (e.g., immigrant communities; Lee et al., 2015).",
        reference_markdown=AUTHOR_YEAR_REFERENCES,
    )
    assert text == (
        "Trust improves - a useful outcome. "
        "Pay rises (relative to the national average). "
        "Groups benefit (e.g., immigrant communities)."
    )
    assert count == 4


@pytest.mark.parametrize(
    "aside",
    [
        "e.g., living abroad",
        "i.e., premature consensus",
        "often, though not always",
        "from 2008 to 2015",
        "May, 2015",
        "FY, 2024",
        "USD, 2024",
        "sales rose, 2015 was a record year",
        "20%, 2014",
        "A + B, 2015",
        "2015",
        "the U.S. is the only country to do so",
        "Annual Revenue, 2024",
        "Revenue, 2024",
        "New York, 2015",
        "Smith, 2015, argues the opposite",
        "see Smith, 2015, for a detailed explanation",
        "see Exhibit 3",
    ],
)
def test_explanatory_parentheses_dates_and_values_survive(aside):
    original = f"Useful content ({aside}) continues."
    assert strip_parenthetical_citations(
        original, reference_markdown=AUTHOR_YEAR_REFERENCES
    ) == (original, 0)


def test_single_name_or_organization_needs_a_matching_reference_year():
    text = "Evidence (Smith, 2015) differs from results (Smith, 2016) and data (ABC, 2024)."
    assert strip_parenthetical_citations(text) == (text, 0)
    assert strip_parenthetical_citations(text, reference_markdown=AUTHOR_YEAR_REFERENCES) == (
        "Evidence differs from results (Smith, 2016) and data (ABC, 2024).", 1
    )


def test_ordinary_dated_body_prose_does_not_count_as_reference_evidence():
    text = "Office opened (New York, 2015)."
    assert strip_parenthetical_citations(
        text, reference_markdown="## History\nNew York, 2015. The office opened."
    ) == (text, 0)


def test_removing_nested_citation_preserves_the_outer_explanation():
    text, count = strip_parenthetical_citations(
        "Helpful context (especially in mixed teams (Smith, 2015)) matters.",
        reference_markdown=AUTHOR_YEAR_REFERENCES,
    )
    assert text == "Helpful context (especially in mixed teams) matters."
    assert count == 1


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
        "Explanatory note: This model assumes two firms. However, pricing can vary.",
        "Explanatory note: Sales grew in 2024 because demand increased.",
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


def test_inline_source_keeps_following_metric_definition():
    result = filter_citation_blocks(
        [block("Sources: Compustat Fundamentals Annual. ROIC is net income divided by capital.")]
    )
    assert [item.text for item in result.blocks] == ["ROIC is net income divided by capital."]


def test_decorated_exhibit_source_line_is_omitted() -> None:
    result = filter_citation_blocks([block("·Source: The Airline Monitor, May 2000 (deliveries).")])

    assert result.blocks == []
    assert result.omitted == 1


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


def test_extraction_always_filters_citations(monkeypatch):
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
    assert "Smith" not in filtered
    assert signals.omitted_citation_blocks == 2


def test_extraction_strips_author_year_citations_after_page_joining(monkeypatch):
    from docling import document_converter

    from case2audio import extractor
    from case2audio.quality import ExtractionSignals

    source = (
        "Teams considered more perspectives (Rivera & Chen,\n\n2019). "
        "The benefit held (relative to baseline; Research Center, 2020)."
    )
    document = SimpleNamespace(
        export_to_markdown=lambda **_: source + "\n\n## References\n"
        "Research Center. (2020). A report.",
        export_to_dict=lambda: {"text": source},
    )
    # Stub parsing, but exercise the real post-Docling cleanup and quality-report path.
    monkeypatch.setattr(
        document_converter,
        "DocumentConverter",
        lambda **_: SimpleNamespace(convert=lambda _: SimpleNamespace(document=document)),
    )
    monkeypatch.setattr(
        extractor,
        "_build_narration_markdown",
        lambda *_args, **_kwargs: (source, ExtractionSignals()),
    )
    fixture = Path(__file__).parent / "fixtures" / "synthetic-case.pdf"
    result = extractor.extract_pdf(fixture, use_ocr=False)
    assert result.narration == (
        "Teams considered more perspectives. The benefit held (relative to baseline).\n"
    )
    assert "Rivera & Chen" in result.markdown  # Debug sources remain reviewable.
    assert "AUTHOR_YEAR_CITATIONS_REMOVED: Removed 2" in result.quality_report.render()


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


def test_mixed_notes_lose_provenance_but_keep_qualifications():
    result = filter_citation_blocks(
        [
            block("Endnotes", "section_header"),
            block(
                '1 Smith, "Costs," 2024. Figures are adjusted to 2026. '
                "Used with attribution as required by a license; confirm permission.",
                "footnote",
            ),
            block('2 Jones, "Costs," 2024. Figures are adjusted to 2026.', "footnote"),
            block(
                '3 Figures exclude freight. Jones, "Costs," 2024. However, revenue includes taxes.',
                "footnote",
            ),
            block(
                "4 This statement, and all others by Tom unless otherwise noted, "
                "are from an interview with case writers in 2024.",
                "footnote",
            ),
            block(
                "5 The previous draft's statement could not be verified and was removed.",
                "footnote",
            ),
        ]
    )
    assert [b.text for b in result.blocks] == [
        "Explanatory notes",
        "Figures are adjusted to 2026.",
        "Figures exclude freight. However, revenue includes taxes.",
    ]


def test_dated_explanations_and_quotes_are_not_deleted():
    text = 'The company reported "record sales" in 2024, despite higher costs.'
    result = filter_citation_blocks([block("1 " + text, "footnote")])
    assert result.blocks[0].text == f"Explanatory note: {text}"


def test_compensation_explanation_with_comma_and_year_survives():
    text = (
        "Retailers controlled both pay and working hours, making annual pay comparable. "
        "In 2024, hourly pay stood at $18 for one retailer and $26 for another."
    )
    result = filter_citation_blocks([block("ii " + text, "footnote")])
    assert [b.text for b in result.blocks] == ["Explanatory note: " + text]


def test_source_initials_and_abbreviations_are_not_explanations():
    sources = [
        "Source: Thomas J. Holmes, 'A Study,' Journal 79 (2011): 253-302.",
        "Source: Pankaj Ghemawat, Stephen P. Bradley, and Ken Mark, A Book (2003).",
        "Source: Walmart Stores, Inc. Annual Report.",
    ]
    assert filter_citation_blocks([block(t) for t in sources]).blocks == []
    result = filter_citation_blocks([block(sources[-1] + " ROIC is income over capital.")])
    assert [b.text for b in result.blocks] == ["ROIC is income over capital."]


def test_split_source_colon_is_omitted_without_swallowing_explanation():
    result = filter_citation_blocks(
        [
            block("Source"),
            block(": Company financial reports"),
            block("The figures exclude tax."),
            block("Source:"),
            block("Useful section", "section_header"),
            block("Real case prose."),
        ]
    )
    assert [b.text for b in result.blocks] == [
        "The figures exclude tax.",
        "Useful section",
        "Real case prose.",
    ]


def test_side_by_side_source_fragments_are_filtered_together():
    result = filter_citation_blocks(
        [
            replace(block("Source: Example"), left=50, right=150),
            replace(block("Stores, Inc. Annual Report."), left=153, right=300),
            replace(block("Actual body prose continues."), top=450, bottom=400),
        ]
    )
    assert [b.text for b in result.blocks] == ["Actual body prose continues."]
