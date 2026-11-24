import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

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
    inputs = [
        tmp_path / "first case.PDF",
        tmp_path / "second case.txt",
        tmp_path / "third case.RTF",
    ]
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
            str(inputs[2]),
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
    assert command[4:7] == [str(path.resolve()) for path in inputs]
    assert command[-2:] == ["--drop-regex", "^confidential course copy$"]
    assert "--no-ocr" in command


def test_wrapper_forwards_no_case_after_input_without_running_aws(tmp_path):
    source = tmp_path / "AITransformationRoles1.rtf"
    source.touch()
    config = tmp_path / "config.env"
    config.write_text("CASE2AUDIO_BUCKET=example\n")

    result = subprocess.run(
        ["bash", str(WRAPPER), "--dry-run", f"./{source.name}", "-nc"],
        env={**os.environ, "CASE2AUDIO_CONFIG": str(config)},
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    command = shlex.split(result.stdout.removeprefix("Would run:"))

    assert command[1:4] == ["-m", "case2audio.cli", "make"]
    assert command[4] == str(source.resolve())
    assert command[-1] == "-nc"


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


def _isolated_wrapper_fixture(tmp_path):
    """Copy just the shell wrapper into an offline checkout with a private home."""
    # A private checkout and HOME keep lookup tests away from real inputs and AWS state.
    repo = tmp_path / "checkout.with dots"
    repo.mkdir()
    shutil.copy(WRAPPER, repo / "make-audio")
    shutil.copytree(REPO_ROOT / "scripts", repo / "scripts")
    (repo / ".case2audio.env").write_text("CASE2AUDIO_BUCKET=test-bucket\n")
    input_dir = repo / "input"
    input_dir.mkdir()
    home = tmp_path / "home.with dots"
    (home / "Downloads").mkdir(parents=True)
    return repo, input_dir, home


def _dry_run_wrapper(repo, home, *args, cwd=None):
    # The wrapper's dry-run path prints arguments without bootstrapping Python or AWS.
    result = subprocess.run(
        ["bash", str(repo / "make-audio"), "--dry-run", *map(str, args)],
        env={
            **os.environ,
            "HOME": str(home),
            "CASE2AUDIO_CONFIG": str(repo / ".case2audio.env"),
        },
        cwd=cwd or repo,
        check=False,
        capture_output=True,
        text=True,
    )
    return result


def _dry_run_command(result):
    assert result.returncode == 0, result.stderr
    return shlex.split(result.stdout.removeprefix("Would run:"))


def test_wrapper_extensionless_lookup_checks_extension_priority_across_both_dirs(tmp_path):
    repo, input_dir, home = _isolated_wrapper_fixture(tmp_path)
    # Extension priority beats folder priority: a Downloads PDF beats input RTF/TXT.
    expected = home / "Downloads" / "case notes.pdf"
    expected.touch()
    (input_dir / "case notes.rtf").touch()
    (input_dir / "case notes.txt").touch()

    command = _dry_run_command(_dry_run_wrapper(repo, home, "case notes"))

    assert command[4] == str(expected.resolve())


def test_wrapper_extensionless_lookup_falls_back_to_rtf_before_txt(tmp_path):
    repo, input_dir, home = _isolated_wrapper_fixture(tmp_path)
    expected = home / "Downloads" / "fallback.rtf"
    expected.touch()
    (input_dir / "fallback.txt").touch()

    command = _dry_run_command(_dry_run_wrapper(repo, home, "fallback"))

    assert command[4] == str(expected.resolve())


def test_wrapper_extensionless_lookup_accepts_uppercase_extension(tmp_path):
    repo, input_dir, home = _isolated_wrapper_fixture(tmp_path)
    # Case-insensitive filesystems satisfy lowercase probes too, so they cannot cover this branch.
    case_probe = input_dir / "case-probe"
    case_probe.touch()
    if (input_dir / "CASE-PROBE").exists():
        pytest.skip("filesystem is case-insensitive")
    expected = input_dir / "legacy.RTF"
    expected.touch()

    command = _dry_run_command(_dry_run_wrapper(repo, home, "legacy"))

    assert command[4] == str(expected.resolve())


def test_wrapper_extensionless_lookup_uses_txt_as_last_fallback(tmp_path):
    repo, input_dir, home = _isolated_wrapper_fixture(tmp_path)
    expected = input_dir / "last resort.txt"
    expected.touch()

    command = _dry_run_command(_dry_run_wrapper(repo, home, "last resort"))

    assert command[4] == str(expected.resolve())


def test_wrapper_extensionless_lookup_prefers_input_for_same_extension(tmp_path):
    repo, input_dir, home = _isolated_wrapper_fixture(tmp_path)
    expected = input_dir / "same name.rtf"
    expected.touch()
    (home / "Downloads" / "same name.rtf").touch()

    command = _dry_run_command(_dry_run_wrapper(repo, home, "same name"))

    assert command[4] == str(expected.resolve())


def test_wrapper_explicit_extension_keeps_exact_file_and_forwards_no_case(tmp_path):
    repo, input_dir, home = _isolated_wrapper_fixture(tmp_path)
    expected = input_dir / "exact choice.rtf"
    expected.touch()
    # Competing extensions must not replace an explicitly named RTF input.
    (input_dir / "exact choice.pdf").touch()
    (home / "Downloads" / "exact choice.pdf").touch()

    command = _dry_run_command(
        _dry_run_wrapper(repo, home, "exact choice.rtf", "-nc")
    )

    assert command[4] == str(expected.resolve())
    assert command[-1] == "-nc"


def test_wrapper_explicit_extensionless_path_searches_only_its_own_directory(tmp_path):
    repo, input_dir, home = _isolated_wrapper_fixture(tmp_path)
    explicit_dir = tmp_path / "client.folder with spaces"
    explicit_dir.mkdir()
    expected = explicit_dir / "meeting.pdf"
    expected.touch()
    (input_dir / "meeting.pdf").touch()
    (home / "Downloads" / "meeting.pdf").touch()

    command = _dry_run_command(
        _dry_run_wrapper(repo, home, "./client.folder with spaces/meeting", cwd=tmp_path)
    )

    assert command[4] == str(expected.resolve())


def test_wrapper_missing_explicit_path_does_not_fall_back_to_downloads(tmp_path):
    repo, input_dir, home = _isolated_wrapper_fixture(tmp_path)
    (input_dir / "meeting.pdf").touch()
    (home / "Downloads" / "meeting.pdf").touch()

    # A missing explicit path must not accidentally submit a different matching file.
    result = _dry_run_wrapper(repo, home, "./missing/meeting", cwd=tmp_path)

    assert result.returncode == 2
    assert "Input not found: ./missing/meeting" in result.stderr
    assert "Would run:" not in result.stdout


def test_wrapper_extensionless_missing_error_lists_attempted_candidates(tmp_path):
    repo, input_dir, home = _isolated_wrapper_fixture(tmp_path)

    result = _dry_run_wrapper(repo, home, "missing case")

    assert result.returncode == 2
    assert "missing case" in result.stderr
    assert ".pdf" in result.stderr
    assert ".rtf" in result.stderr
    assert ".txt" in result.stderr
    assert "input/" in result.stderr
    assert "~/Downloads" in result.stderr


def test_wrapper_missing_extensionless_item_in_batch_stops_before_cli_start(tmp_path):
    repo, input_dir, home = _isolated_wrapper_fixture(tmp_path)
    first = input_dir / "valid.pdf"
    first.touch()

    result = _dry_run_wrapper(repo, home, first, "missing case")

    assert result.returncode == 2
    assert "Input not found" in result.stderr
    assert "Would run:" not in result.stdout
