from case2audio.cli import build_parser


def test_extract_defaults_are_narration_friendly() -> None:
    args = build_parser().parse_args(["extract", "case.pdf"])

    assert args.table_mode == "skip"
    assert args.no_ocr is False
