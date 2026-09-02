#!/usr/bin/env bash
set -euo pipefail

# Prefer the requested interpreter, while allowing CI or another machine to override it.
python_bin="${CASE2AUDIO_PYTHON:-python3.12}"
if ! command -v "$python_bin" >/dev/null 2>&1; then
  echo "Could not find $python_bin. Install Python 3.12 or set CASE2AUDIO_PYTHON." >&2
  exit 1
fi

# Keeping everything in .venv makes setup repeatable and cleanup painless.
"$python_bin" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'

echo "Setup complete. Run: source .venv/bin/activate"
