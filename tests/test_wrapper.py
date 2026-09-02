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
