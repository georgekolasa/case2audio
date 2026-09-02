from case2audio.cli import build_parser


def test_extract_defaults_are_narration_friendly() -> None:
    args = build_parser().parse_args(["extract", "case.pdf"])

    assert args.table_mode == "skip"
    assert args.no_ocr is False


def test_polly_defaults_to_the_broadly_supported_standard_engine() -> None:
    args = build_parser().parse_args(["speak", "case.txt", "--bucket", "example"])

    assert args.engine == "standard"
