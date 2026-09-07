import os
import shlex
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = REPO_ROOT / "make-audio"


def test_wrapper_help_does_not_require_aws_or_a_pdf() -> None:
    result = subprocess.run(
        ["bash", str(WRAPPER), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "./make-audio [--ocr] [--dry-run] PDF" in result.stdout


def test_wrapper_rejects_a_missing_pdf_before_calling_aws() -> None:
    result = subprocess.run(
        ["bash", str(WRAPPER), "definitely-missing.pdf"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "PDF not found" in result.stderr


def test_wrapper_forwards_multiple_paths_and_shared_options(tmp_path):
    pdfs = [tmp_path / "first case.pdf", tmp_path / "second case.pdf"]
    for pdf in pdfs:
        pdf.touch()
    config = tmp_path / "config.env"
    config.write_text("CASE2AUDIO_BUCKET=example\nCASE2AUDIO_PROFILE=test-profile\n")
    result = subprocess.run(
        [
            "bash",
            str(WRAPPER),
            "--dry-run",
            f"./{pdfs[0].name}",
            str(pdfs[1]),
            "--drop-regex",
            "^confidential course copy$",
        ],
        env={**os.environ, "CASE2AUDIO_CONFIG": str(config)},
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    command = shlex.split(result.stdout.removeprefix("Would run:"))
    assert command[1:4] == ["make", *(str(pdf.resolve()) for pdf in pdfs)]
    assert command[-2:] == ["--drop-regex", "^confidential course copy$"]
    assert "--no-ocr" in command


def test_wrapper_checks_second_file_before_launching_cli(tmp_path):
    first = tmp_path / "valid.pdf"
    first.touch()
    result = subprocess.run(
        ["bash", str(WRAPPER), str(first), str(tmp_path / "missing.pdf")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "PDF not found:" in result.stderr
