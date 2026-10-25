#!/usr/bin/env bash
set -euo pipefail

# Resolve from the script, not the caller's working directory or an activated environment.
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=runtime.sh
source "$repo_dir/scripts/runtime.sh"
cd "$repo_dir"

install_target="$repo_dir"
case "${1:-}" in
  --dev) install_target="$repo_dir[dev]"; shift ;;
  -h|--help)
    echo "Usage: ./scripts/bootstrap.sh [--dev]"
    echo "Install Python and runtime dependencies automatically; --dev adds test tools."
    exit 0
    ;;
esac
if [[ $# -ne 0 ]]; then
  echo "Usage: ./scripts/bootstrap.sh [--dev]" >&2
  exit 2
fi

# uv finds compatible Python installations and downloads the pinned version if absent.
uv_bin="$(command -v uv || true)"
if [[ -z "$uv_bin" ]]; then
  for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if [[ -x "$candidate" ]]; then
      uv_bin="$candidate"
      break
    fi
  done
fi
if [[ -z "$uv_bin" ]]; then
  echo "Installing uv to manage Python automatically..."
  # Use the official installer without changing shell profiles or requiring a new terminal.
  uv_installer="$(mktemp)"
  trap 'rm -f -- "$uv_installer"' EXIT
  curl --fail --silent --show-error --location --retry 3 \
    https://astral.sh/uv/install.sh --output "$uv_installer"
  UV_UNMANAGED_INSTALL="$HOME/.local/bin" sh "$uv_installer"
  uv_bin="$HOME/.local/bin/uv"
fi

if ! case2audio_python_ready "$repo_dir"; then
  if [[ -e "$repo_dir/.venv" || -L "$repo_dir/.venv" ]]; then
    # Preserve a moved/broken environment for recovery instead of deleting someone's files.
    backup_dir="$(mktemp -d "$repo_dir/.venv-backup.XXXXXX")"
    mv -- "$repo_dir/.venv" "$backup_dir/.venv"
    echo "Preserved the old environment in $backup_dir/.venv"
  fi
  python_request="${CASE2AUDIO_PYTHON:-$(<"$repo_dir/.python-version")}"
  echo "Preparing Python $python_request (downloaded automatically if needed)..."
  # Seed pip so the documented developer commands work in this environment too.
  "$uv_bin" venv --seed --python "$python_request" "$repo_dir/.venv"
fi

echo "Installing case2audio dependencies; the first setup can take a few minutes..."
# A regular install avoids editable .pth issues in some bundled Python builds.
"$uv_bin" pip install --python "$repo_dir/.venv/bin/python" "$install_target"
if ! case2audio_runtime_ready "$repo_dir"; then
  echo "Setup did not produce a usable environment. Rerun ./scripts/bootstrap.sh to retry." >&2
  exit 1
fi
echo "Setup complete. Run ./make-audio with your filenames; no activation needed."
