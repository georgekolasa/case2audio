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
