from case2audio.quality import ExtractionSignals, assess_narration


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
