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

    assert "./make-audio [--ocr] [--dry-run] INPUT" in result.stdout


def test_wrapper_rejects_a_missing_input_before_calling_aws() -> None:
    result = subprocess.run(
        ["bash", str(WRAPPER), "definitely-missing.pdf"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "Input not found" in result.stderr


def test_wrapper_forwards_mixed_inputs_and_shared_options(tmp_path):
    inputs = [tmp_path / "first case.PDF", tmp_path / "second case.txt"]
    for path in inputs:
        path.touch()
    config = tmp_path / "config.env"
    config.write_text("CASE2AUDIO_BUCKET=example\nCASE2AUDIO_PROFILE=test-profile\n")
    result = subprocess.run(
        [
            "bash",
            str(WRAPPER),
            "--dry-run",
            f"./{inputs[0].name}",
            str(inputs[1]),
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
    assert command[1:4] == ["make", *(str(path.resolve()) for path in inputs)]
    assert command[-2:] == ["--drop-regex", "^confidential course copy$"]
    assert "--no-ocr" in command


def test_wrapper_checks_second_input_before_launching_cli(tmp_path):
    first = tmp_path / "valid.pdf"
    first.touch()
    result = subprocess.run(
        ["bash", str(WRAPPER), str(first), str(tmp_path / "missing.pdf")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Input not found:" in result.stderr


def test_wrapper_prefers_matching_repo_input_for_bare_name_and_keeps_explicit_path(tmp_path):
    # Use a unique fixture in the real input directory to cover the wrapper's preferred lookup.
    repo_input = REPO_ROOT / "input"
    created_input_dir = not repo_input.exists()
    repo_input.mkdir(exist_ok=True)
    bare = repo_input / f"wrapper-test-{os.getpid()}.txt"
    explicit = tmp_path / "explicit case.pdf"
    config = tmp_path / "config.env"
    try:
        bare.touch()
        explicit.touch()
        config.write_text("CASE2AUDIO_BUCKET=example\n")
        result = subprocess.run(
            ["bash", str(WRAPPER), "--dry-run", bare.name, str(explicit)],
            env={**os.environ, "CASE2AUDIO_CONFIG": str(config)},
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        bare.unlink(missing_ok=True)
        if created_input_dir:
            repo_input.rmdir()
    command = shlex.split(result.stdout.removeprefix("Would run:"))
    assert command[1:4] == [
        "make",
        str(bare.resolve()),
        str(explicit.resolve()),
    ]
