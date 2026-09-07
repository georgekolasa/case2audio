"""Command-line interface for extraction and Polly synthesis."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .errors import Case2AudioError


def build_parser() -> argparse.ArgumentParser:
    """Build the parser separately so help behavior is easy to test."""

    parser = argparse.ArgumentParser(
        prog="case2audio",
        description="Extract narration-ready text from PDFs and send it to Amazon Polly.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract = subparsers.add_parser("extract", help="Create narration text locally; no AWS calls.")
    _add_pdf_argument(extract)
    extract.add_argument("-o", "--output", type=Path, help="Narration .txt path.")
    extract.add_argument("--debug-dir", type=Path, help="Also save Docling Markdown and JSON.")
    extract.add_argument("--no-ocr", action="store_true", help="Skip OCR for born-digital PDFs.")
    extract.add_argument(
        "--table-mode",
        choices=("smart", "skip", "linearize"),
        default="smart",
        help="Narrate text tables and mark dense data tables, skip all, or read every cell.",
    )
    extract.add_argument(
        "--drop-regex",
        action="append",
        default=[],
        help="Extra case-insensitive full-line regex to omit; repeat as needed.",
    )
    extract.set_defaults(handler=_handle_extract)

    speak = subparsers.add_parser("speak", help="Send an existing text file to Polly.")
    speak.add_argument("text", type=Path)
    _add_polly_arguments(speak)
    speak.set_defaults(handler=_handle_speak)

    make = subparsers.add_parser("make", help="Extract a PDF, synthesize it, and download audio.")
    _add_pdf_argument(make)
    _add_polly_arguments(make)
    make.add_argument("--no-ocr", action="store_true", help="Skip OCR for born-digital PDFs.")
    make.add_argument(
        "--table-mode",
        choices=("smart", "skip", "linearize"),
        default="smart",
    )
    make.add_argument("--drop-regex", action="append", default=[])
    make.set_defaults(handler=_handle_make)

    doctor = subparsers.add_parser("doctor", help="Check local and AWS prerequisites.")
    doctor.add_argument("--profile")
    doctor.add_argument("--region")
    doctor.set_defaults(handler=_handle_doctor)
    return parser


def _add_pdf_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("pdf", type=Path, help="Source PDF; it is never copied into the repo.")


def _add_polly_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bucket", required=True, help="Writable S3 bucket in the Polly region.")
    parser.add_argument("--region", help="AWS region; otherwise use the normal AWS config.")
    parser.add_argument("--profile", help="Named AWS profile; otherwise use the default chain.")
    parser.add_argument("--prefix", default="case2audio", help="Temporary S3 key prefix.")
    parser.add_argument("--voice", default="Matthew")
    parser.add_argument(
        "--engine",
        choices=("standard", "neural", "long-form", "generative"),
        default="generative",
    )
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("generated"))


def _handle_extract(args: argparse.Namespace) -> int:
    from .extractor import extract_pdf, write_extraction

    output = args.output or Path("generated") / f"{args.pdf.stem}.txt"
    result = extract_pdf(
        args.pdf,
        use_ocr=not args.no_ocr,
        table_mode=args.table_mode,
        extra_drop_patterns=tuple(args.drop_regex),
    )
    write_extraction(result, output, debug_dir=args.debug_dir)
    print(f"Saved narration: {output.resolve()} ({len(result.narration):,} characters)")
    _print_quality(result.quality_report)
    return 0


def _handle_speak(args: argparse.Namespace) -> int:
    from .auth import prepare_session
    from .polly import synthesize_to_directory
    from .quality import ExtractionSignals, assess_narration

    if not args.text.is_file():
        raise Case2AudioError(f"Text file not found: {args.text}")
    text = args.text.read_text(encoding="utf-8")
    quality_report = assess_narration(text, signals=ExtractionSignals())
    _print_quality(quality_report)
    _enforce_quality(quality_report)
    session = prepare_session(profile=args.profile, region=args.region)
    parts = synthesize_to_directory(text, args.output_dir, _polly_options(args), session=session)
    _print_parts(parts)
    return 0


def _handle_make(args: argparse.Namespace) -> int:
    from .auth import prepare_session
    from .extractor import extract_pdf, write_extraction
    from .polly import _validate_voice_engine, synthesize_to_directory

    if not args.pdf.is_file():
        raise Case2AudioError(f"PDF not found: {args.pdf}")
    # Resolve login and voice errors before loading models or processing a long PDF.
    session = prepare_session(profile=args.profile, region=args.region)
    _validate_voice_engine(session.client("polly"), _polly_options(args))
    job_dir = args.output_dir / args.pdf.stem
    result = extract_pdf(
        args.pdf,
        use_ocr=not args.no_ocr,
        table_mode=args.table_mode,
        extra_drop_patterns=tuple(args.drop_regex),
    )
    narration_path = job_dir / "narration.txt"
    write_extraction(result, narration_path, debug_dir=job_dir / "debug")
    print(f"Saved narration: {narration_path.resolve()} ({len(result.narration):,} characters)")
    _print_quality(result.quality_report)
    # Save all local diagnostics first, but never submit unsafe text to a paid service.
    _enforce_quality(result.quality_report)
    parts = synthesize_to_directory(
        result.narration, job_dir / "audio", _polly_options(args), session=session
    )
    _print_parts(parts)
    return 0


def _polly_options(args: argparse.Namespace):
    from .polly import PollyOptions

    return PollyOptions(
        bucket=args.bucket,
        region=args.region,
        profile=args.profile,
        prefix=args.prefix,
        voice=args.voice,
        engine=args.engine,
    )


def _print_parts(parts) -> None:
    print(f"Downloaded {len(parts)} audio part(s):")
    for part in parts:
        print(f"  {part.path.resolve()}")
    if len(parts) > 1:
        # Avoid silently producing a corrupt joined MP3 when ffmpeg is unavailable.
        print("The document exceeded Polly's task limit, so audio remains in ordered parts.")


def _print_quality(report) -> None:
    print(report.render().rstrip())


def _enforce_quality(report) -> None:
    if report.blocking:
        codes = ", ".join(finding.code for finding in report.blocking)
        raise Case2AudioError(
            f"Narration failed the pre-Polly quality gate ({codes}); no synthesis task submitted."
        )


def _handle_doctor(args: argparse.Namespace) -> int:
    checks: list[tuple[str, bool, str]] = []
    checks.append(("Python 3.10+", sys.version_info >= (3, 10), sys.version.split()[0]))

    try:
        import docling

        checks.append(("Docling", True, getattr(docling, "__version__", "installed")))
    except ImportError as exc:
        checks.append(("Docling", False, str(exc)))

    try:
        import boto3

        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        identity = session.client("sts").get_caller_identity()
        checks.append(("AWS credentials", True, identity["Arn"]))
        region = session.region_name or "not configured"
        checks.append(("AWS region", session.region_name is not None, region))
    except Exception as exc:
        checks.append(("AWS credentials", False, str(exc)))

    for label, ok, detail in checks:
        print(f"{'OK' if ok else 'WARN':4}  {label}: {detail}")
    return 0 if all(ok for _, ok, _ in checks) else 1


def main(argv: list[str] | None = None) -> int:
    """Return a shell-friendly exit code and keep expected failures readable."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (Case2AudioError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
