#!/usr/bin/env bash
# Shared checks keep setup and normal runs in agreement about a usable environment.

case2audio_python_ready() {
  local repo_dir="$1"
  [[ -x "$repo_dir/.venv/bin/python" ]] || return 1
  # A copied environment may contain a broken Python link or resolve to the old location.
  "$repo_dir/.venv/bin/python" -c '
import sys
from pathlib import Path
raise SystemExit(not (
    sys.version_info >= (3, 10)
    and Path(sys.prefix).resolve() == Path(sys.argv[1]).resolve()
))
' "$repo_dir/.venv" >/dev/null 2>&1
}

case2audio_runtime_ready() {
  local repo_dir="$1"
  case2audio_python_ready "$repo_dir" || return 1
  # Checking module locations is cheap; importing Docling would load the model stack every run.
  "$repo_dir/.venv/bin/python" -c '
import sys
from importlib.util import find_spec
modules = ["case2audio", "docling", "boto3", "pypdfium2"]
if sys.platform == "darwin":
    modules.append("ocrmac")
raise SystemExit(not all(find_spec(module) is not None for module in modules))
' >/dev/null 2>&1
}
