"""Keep recent completed cases and move stale generated case folders to macOS Trash."""

from __future__ import annotations

import fcntl
import json
import math
import os
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

KEEP_CASES = 5
MIN_AGE_SECONDS = 2 * 24 * 60 * 60
STATE_FILE = ".audio-state.json"


@contextmanager
def _locked(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    # Coordinate separate make-audio processes as well as the workers in one batch.
    with (root / ".retention.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _read_state(folder: Path):
    path = folder / STATE_FILE
    if not path.exists():
        return None
    # Malformed state is not evidence that a case is safe to discard.
    try:
        state = json.loads(path.read_text())
        return state if isinstance(state, dict) else {"status": "unknown"}
    except (OSError, ValueError):
        return {"status": "unknown"}


def _write_state(folder: Path, state: dict):
    temporary = folder / f".audio-state-{uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps(state) + "\n")
        temporary.replace(folder / STATE_FILE)
    finally:
        temporary.unlink(missing_ok=True)


def begin_batch(root: Path, names: list[str]) -> str:
    token = uuid4().hex
    with _locked(root):
        for name in names:
            folder = root / name
            if folder.is_symlink() or folder.resolve().parent != root.resolve():
                raise ValueError(f"Unsafe case output folder: {folder}")
            state = _read_state(folder)
            if state and state.get("status") == "running":
                try:
                    os.kill(int(state["pid"]), 0)
                except ProcessLookupError:
                    pass  # A crashed run may be explicitly retried, but never auto-pruned.
                else:
                    raise ValueError(f"Another make-audio run is already using {folder}")
        for name in names:
            folder = root / name
            folder.mkdir(parents=True, exist_ok=True)
            # Mark every input before extraction so old MP3s from a rerun cannot qualify.
            _write_state(folder, {"status": "running", "pid": os.getpid(), "token": token})
    return token


def complete_case(root: Path, name: str, token: str, parts) -> None:
    with _locked(root):
        folder = root / name
        state = _read_state(folder)
        if not state or state.get("token") != token:
            raise ValueError(f"Case completion belongs to a different run: {folder}")
        if not parts:
            return
        files = [part.path.resolve().relative_to(folder.resolve()).as_posix() for part in parts]
        if any(
            not (folder / file).is_file() or (folder / file).stat().st_size <= 0 for file in files
        ):
            raise ValueError(f"Completed audio is missing or empty: {folder}")
        _write_state(folder, {"status": "complete", "completed_at": time.time(), "files": files})


def finish_batch(root: Path, token: str) -> None:
    with _locked(root):
        for folder in root.iterdir():
            if folder.is_symlink() or not folder.is_dir():
                continue
            state = _read_state(folder)
            if state and state.get("token") == token:
                _write_state(folder, {"status": "incomplete"})


def _candidate(folder: Path):
    if folder.is_symlink() or not folder.is_dir() or not (folder / "narration.txt").is_file():
        return None
    contents = list(folder.rglob("*"))
    # Never traverse a linked output into the user's other data or prune an active partial file.
    if any(p.is_symlink() or p.name.endswith(".partial") for p in contents):
        return None
    state = _read_state(folder)
    if state is not None:
        if state.get("status") != "complete":
            return None
        files = state.get("files", [])
        completed = float(state["completed_at"])
        if not isinstance(files, list) or not files or not math.isfinite(completed):
            return None
        audio = [folder / file for file in files]
        if any(not p.resolve().is_relative_to(folder.resolve()) for p in audio):
            return None
    else:
        # Older versions had no completion record. Require every expected, numbered MP3 part.
        from .polly import split_for_polly

        count = len(split_for_polly((folder / "narration.txt").read_text()))
        if count == 0:
            return None
        named = folder / f"{folder.name} Case"
        audio = [
            named
            / (f"{folder.name}Case.mp3" if count == 1 else f"{folder.name}Case-part-{i:03d}.mp3")
            for i in range(1, count + 1)
        ]
        if not all(p.is_file() for p in audio):
            audio = [folder / "audio" / f"part-{i:03d}.mp3" for i in range(1, count + 1)]
        if not all(p.is_file() for p in audio):
            return None
        completed = max(p.stat().st_mtime for p in audio)
    if any(not p.is_file() or p.stat().st_size <= 0 for p in audio):
        return None
    # Recent edits and reruns get the same two-day protection as newly completed audio.
    latest_activity = max([completed, *(p.stat().st_mtime for p in contents)])
    return completed, latest_activity


def prune_cases(root: Path, *, now: float | None = None, trash: Path | None = None) -> list[Path]:
    now = time.time() if now is None else now
    trash = Path.home() / ".Trash" if trash is None else trash
    moved = []
    with _locked(root):
        candidates = []
        for folder in root.iterdir():
            try:
                candidate = _candidate(folder)
                if candidate is not None:
                    candidates.append((candidate[0], folder.name, candidate[1], folder))
            except (OSError, ValueError, KeyError, TypeError):
                continue  # Uncertain or malformed folders are never deletion candidates.
        candidates.sort(reverse=True)
        for _, _, latest_activity, folder in candidates[KEEP_CASES:]:
            if now - latest_activity <= MIN_AGE_SECONDS:
                continue
            trash.mkdir(parents=True, exist_ok=True)
            target = trash / f"case2audio-{folder.name}-{uuid4().hex}"
            shutil.move(str(folder), str(target))
            moved.append(target)
            print(f"Local cleanup: moved {folder.name} to Trash (older than 2 days).", flush=True)
    return moved
