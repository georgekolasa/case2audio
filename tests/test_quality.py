from case2audio.quality import ExtractionSignals, assess_narration


def test_corrupt_font_codes_are_blocked_before_polly():
    import pytest

    from case2audio.cli import _enforce_quality
    from case2audio.errors import Case2AudioError

    # Synthetic excerpt of the Valuation1 failure, without licensed case text.
    report = assess_narration("161 162 163 i255 " * 100, signals=ExtractionSignals())
    assert "CORRUPT_TEXT" in report.render()
    with pytest.raises(Case2AudioError):
        _enforce_quality(report)


def test_financial_prose_is_not_mistaken_for_font_codes():
    text = "In 2024, project 1 cost $100 million and returned 12.5% over 10 years. " * 100
    assert not assess_narration(text, signals=ExtractionSignals()).blocking


def test_empty_extraction_and_numeric_dump_are_blocked():
    for text in ("", "123 234 345 " * 100, "\x04\x05\x06 word " * 100):
        assert "CORRUPT_TEXT" in assess_narration(text, signals=ExtractionSignals()).render()


def test_quality_gate_blocks_redaction_leaks_and_running_footers() -> None:
    report = assess_narration(
        "Private Person\n\nPage 9 | Example Case\n",
        signals=ExtractionSignals(redacted_text_items=1),
        hidden_texts=("Private Person",),
    )

    assert {finding.code for finding in report.blocking} == {
        "PAGE_FOOTER",
        "REDACTION_LEAK",
    }
    assert report.render().startswith("Narration quality: BLOCKED")


def test_quality_report_explains_safe_redactions_and_intentional_omissions() -> None:
    report = assess_narration(
        "Employee: [redacted]\n",
        signals=ExtractionSignals(
            redacted_text_items=1,
            narrated_text_tables=1,
            omitted_data_tables=2,
            marked_visuals=2,
        ),
        hidden_texts=("Private Person",),
    )

    assert report.blocking == ()
    assert report.render().startswith("Narration quality: PASS WITH WARNINGS")
    assert "REDACTION_SCRUBBED" in report.render()
    assert "TEXT_TABLES" in report.render()
    assert "DATA_TABLES" in report.render()
    assert "VISUALS" in report.render()


def test_quality_catches_reverse_footers_and_flags_uncertain_damage():
    report = assess_narration(
        "Example Case | Page 4\n\nThe claim. 1\n\nNvidia VIDIA builds chips. "
        "Another damag ed word and unusualword s. Less than 20% 1 of firms.",
        signals=ExtractionSignals(),
    )
    assert {f.code for f in report.blocking} == {"PAGE_FOOTER"}
    assert {f.code for f in report.findings} >= {
        "POSSIBLE_INLINE_CITATIONS",
        "POSSIBLE_SPLIT_WORDS",
        "POSSIBLE_DUPLICATED_WORDS",
    }


def test_normal_quantities_do_not_trigger_citation_warning():
    report = assess_narration(
        "Firm 1 earned 20% in 2024. See Exhibit 3. Revenue was 1.5 million.",
        signals=ExtractionSignals(),
    )
    assert "POSSIBLE_INLINE_CITATIONS" not in report.render()


def test_quality_gate_blocks_private_use_pdf_glyphs() -> None:
    report = assess_narration(
        "Firm \ue001 Competitor",
        signals=ExtractionSignals(),
    )

    assert {finding.code for finding in report.blocking} == {"UNSPOKEN_GLYPHS"}


def test_damaged_sentence_is_flagged_without_inventing_a_repair():
    report = assess_narration(
        "Stores grew quickly; s (see Exhibit 5).", signals=ExtractionSignals()
    )
    assert "DAMAGED_SENTENCE" in report.render()
