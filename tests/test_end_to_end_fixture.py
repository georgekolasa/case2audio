"""Portable full-pipeline coverage using a tiny, unlicensed committed PDF."""

from pathlib import Path

import pytest

from case2audio.extractor import extract_pdf

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic-case.pdf"


@pytest.mark.filterwarnings("ignore:This field is deprecated.*:DeprecationWarning")
def test_synthetic_pdf_runs_through_the_complete_extraction_pipeline():
    result = extract_pdf(FIXTURE, use_ocr=False)
    text = result.narration

    # These assertions cross PDF parsing, geometry cleanup, reading order and narration cleanup.
    assert text.startswith("Synthetic Retail Case\n")
    assert "$1.4 trillion" in text
    assert "70% margin" in text
    assert "proposal with Atlas Partners" in text
    assert "Guiding Questions" in text
    assert "Customers supported the measured expansion." in text

    # None of this visual navigation or citation material should reach an audio script.
    assert "EXAMPLE CASE SERIES" not in text
    assert "Synthetic Retail Case | Page" not in text
    assert "Endnotes" not in text
    assert "Synthetic source describing" not in text
    assert not result.quality_report.blocking
