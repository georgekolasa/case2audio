import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "case2audio-copy"
    repo.mkdir()
    (repo / ".python-version").write_text("3.12\n")
    (repo / "pyproject.toml").write_text("[project]\nname = 'case2audio'\n")
    shutil.copytree(REPO_ROOT / "scripts", repo / "scripts")
    return repo


def _fake_uv(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "uv.log"
    uv = bin_dir / "uv"
    # Recording calls while faking the venv keeps setup tests offline.
    uv.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$CASE2AUDIO_UV_LOG"\n'
        'if [ "$1" = venv ]; then\n'
        '  for arg do venv_path="$arg"; done\n'
        '  mkdir -p "$venv_path/bin"\n'
        '  printf "#!/bin/sh\\nexit 0\\n" > "$venv_path/bin/python"\n'
        '  chmod +x "$venv_path/bin/python"\n'
        'fi\n'
    )
    uv.chmod(0o755)
    return uv, log


def _run_bootstrap(repo: Path, uv: Path, log: Path, tmp_path: Path, *args: str):
    env = {
        **os.environ,
        "PATH": f"{uv.parent}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "CASE2AUDIO_UV_LOG": str(log),
    }
    return subprocess.run(
        ["bash", str(repo / "scripts" / "bootstrap.sh"), *args],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_bootstrap_uses_uv_python_version_from_another_working_directory(tmp_path):
    repo = _fake_repo(tmp_path)
    uv, log = _fake_uv(tmp_path)

    result = _run_bootstrap(repo, uv, log, tmp_path)

    assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    assert any(call.startswith("venv --seed --python 3.12 ") for call in calls)
    assert any(
        call.startswith("pip install --python ") and call.endswith(str(repo)) for call in calls
    )
    assert (repo / ".venv" / "bin" / "python").exists()


def test_bootstrap_dev_installs_the_optional_dev_dependencies(tmp_path):
    repo = _fake_repo(tmp_path)
    uv, log = _fake_uv(tmp_path)

    result = _run_bootstrap(repo, uv, log, tmp_path, "--dev")

    assert result.returncode == 0, result.stderr
    assert any(call.endswith(f"{repo}[dev]") for call in log.read_text().splitlines())


def test_bootstrap_preserves_broken_existing_venv_in_backup(tmp_path):
    repo = _fake_repo(tmp_path)
    broken = repo / ".venv"
    broken.mkdir()
    (broken / "keep-me").write_text("broken environment evidence")
    uv, log = _fake_uv(tmp_path)

    result = _run_bootstrap(repo, uv, log, tmp_path)

    assert result.returncode == 0, result.stderr
    backups = list(repo.glob(".venv-backup.*"))
    assert len(backups) == 1
    assert (backups[0] / ".venv" / "keep-me").read_text() == "broken environment evidence"
    assert (repo / ".venv" / "bin" / "python").exists()
