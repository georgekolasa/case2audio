import os
import shlex
import shutil
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


def test_wrapper_dry_run_needs_no_venv_or_bootstrap(tmp_path):
    # A throwaway checkout proves the fast path does not depend on this machine's venv.
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(WRAPPER, repo / "make-audio")
    shutil.copytree(REPO_ROOT / "scripts", repo / "scripts")
    source = tmp_path / "example.pdf"
    source.touch()
    config = tmp_path / "config.env"
    config.write_text("CASE2AUDIO_BUCKET=example\n")

    result = subprocess.run(
        ["bash", str(repo / "make-audio"), "--dry-run", str(source)],
        env={**os.environ, "CASE2AUDIO_CONFIG": str(config)},
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Would run:" in result.stdout
    assert not (repo / ".venv").exists()


def test_wrapper_bootstraps_missing_runtime_and_invokes_python_module(tmp_path):
    # Fake setup and Python keep this normal-run path offline and clear of AWS calls.
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(WRAPPER, repo / "make-audio")
    shutil.copytree(REPO_ROOT / "scripts", repo / "scripts")
    source = tmp_path / "example.pdf"
    source.touch()
    config = tmp_path / "config.env"
    config.write_text("CASE2AUDIO_BUCKET=example\n")
    bootstrap_log = tmp_path / "bootstrap.log"
    python_log = tmp_path / "python.log"
    (repo / "scripts" / "bootstrap.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        'repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"\n'
        'printf "called\\n" >> "$CASE2AUDIO_BOOTSTRAP_LOG"\n'
        'mkdir -p "$repo_dir/.venv/bin"\n'
        'cat > "$repo_dir/.venv/bin/python" <<\'PYTHON\'\n'
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$CASE2AUDIO_PYTHON_LOG"\n'
        "exit 0\n"
        "PYTHON\n"
        'chmod +x "$repo_dir/.venv/bin/python"\n'
    )
    (repo / "scripts" / "bootstrap.sh").chmod(0o755)

    result = subprocess.run(
        ["bash", str(repo / "make-audio"), str(source)],
        env={
            **os.environ,
            "CASE2AUDIO_CONFIG": str(config),
            "CASE2AUDIO_BOOTSTRAP_LOG": str(bootstrap_log),
            "CASE2AUDIO_PYTHON_LOG": str(python_log),
        },
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert bootstrap_log.read_text().splitlines() == ["called"]
    assert any(
        call.startswith("-m case2audio.cli make ") and str(source.resolve()) in call
        for call in python_log.read_text().splitlines()
    )


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
    assert command[1:4] == ["-m", "case2audio.cli", "make"]
    assert command[4:6] == [str(path.resolve()) for path in inputs]
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
    assert command[1:4] == ["-m", "case2audio.cli", "make"]
    assert command[4:6] == [str(bare.resolve()), str(explicit.resolve())]
