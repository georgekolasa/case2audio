from unittest.mock import Mock

import pytest

from case2audio.cli import _enforce_quality, build_parser
from case2audio.errors import Case2AudioError
from case2audio.quality import ExtractionSignals, assess_narration


def test_make_checks_auth_before_extraction(tmp_path, monkeypatch):
    from case2audio import auth, cli, extractor

    pdf = tmp_path / "case.pdf"
    pdf.touch()
    check = Mock(side_effect=Case2AudioError("expired session"))
    extract = Mock()
    monkeypatch.setattr(auth, "prepare_session", check)
    monkeypatch.setattr(extractor, "extract_pdf", extract)
    args = build_parser().parse_args(
        [
            "make",
            str(pdf),
            "--bucket",
            "example",
            "--profile",
            "school",
            "--region",
            "us-east-1",
        ]
    )
    with pytest.raises(Case2AudioError, match="expired session"):
        cli._handle_make(args)
    check.assert_called_once_with(profile="school", region="us-east-1")
    extract.assert_not_called()


def test_extract_defaults_are_narration_friendly() -> None:
    args = build_parser().parse_args(["extract", "case.pdf"])

    assert args.table_mode == "smart"
    assert args.no_ocr is False


def test_polly_defaults_to_requested_generative_matthew_voice() -> None:
    args = build_parser().parse_args(["speak", "case.txt", "--bucket", "example"])

    assert args.engine == "generative"
    assert args.voice == "Matthew"


def test_pre_polly_gate_rejects_blocking_quality_findings() -> None:
    report = assess_narration(
        "Page 9 | Example Case\n",
        signals=ExtractionSignals(),
    )

    with pytest.raises(Case2AudioError, match="no synthesis task submitted"):
        _enforce_quality(report)
