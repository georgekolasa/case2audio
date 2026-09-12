import json
import os
from types import SimpleNamespace

import pytest

from case2audio import retention

NOW = 2_000_000_000
DAY = 86400


def make_case(root, name, age, *, status="complete", legacy=False):
    folder = root / name
    audio = folder / f"{name} Case" / f"{name}Case.mp3"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"test audio")
    (folder / "narration.txt").write_text("Case prose.")
    (folder / "debug.json").write_text("{}")
    if not legacy:
        (folder / retention.STATE_FILE).write_text(
            json.dumps(
                {
                    "status": status,
                    "completed_at": NOW - age,
                    "files": [audio.relative_to(folder).as_posix()],
                }
            )
        )
    for path in [*folder.rglob("*"), folder]:
        os.utime(path, (NOW - age, NOW - age))
    return folder, audio


def test_keeps_five_newest_and_moves_whole_older_cases_to_trash(tmp_path):
    root, trash = tmp_path / "generated", tmp_path / "trash"
    for i in range(7):
        make_case(root, f"case{i}", (i + 3) * DAY)
    moved = retention.prune_cases(root, now=NOW, trash=trash)
    assert len(moved) == 2
    assert {p.name for p in root.iterdir() if p.is_dir()} == {f"case{i}" for i in range(5)}
    # The entire case remains recoverable, not just its MP3.
    for folder in moved:
        assert (folder / "narration.txt").is_file()
        assert (folder / "debug.json").is_file()
        assert len(list(folder.rglob("*.mp3"))) == 1


def test_super_day_keeps_all_ten_recent_cases(tmp_path):
    for i in range(10):
        make_case(tmp_path / "generated", f"case{i}", i * 3600)
    assert retention.prune_cases(tmp_path / "generated", now=NOW, trash=tmp_path / "trash") == []


@pytest.mark.parametrize("age,removed", [(2 * DAY, False), (2 * DAY + 1, True)])
def test_age_boundary_is_strictly_more_than_48_hours(tmp_path, age, removed):
    root = tmp_path / "generated"
    for i in range(5):
        make_case(root, f"recent{i}", i)
    folder, _ = make_case(root, "old", age)
    retention.prune_cases(root, now=NOW, trash=tmp_path / "trash")
    assert folder.exists() is not removed


@pytest.mark.parametrize("status", ["incomplete", "running", "unknown"])
def test_unfinished_cases_are_never_pruned_or_counted(tmp_path, status):
    root = tmp_path / "generated"
    for i in range(5):
        make_case(root, f"recent{i}", i)
    folder, _ = make_case(root, "unfinished", 9 * DAY, status=status)
    assert retention.prune_cases(root, now=NOW, trash=tmp_path / "trash") == []
    assert folder.is_dir()


def test_recent_edit_protects_old_audio(tmp_path):
    root = tmp_path / "generated"
    for i in range(5):
        make_case(root, f"recent{i}", i)
    folder, _ = make_case(root, "old", 9 * DAY)
    os.utime(folder / "narration.txt", (NOW, NOW))
    assert retention.prune_cases(root, now=NOW, trash=tmp_path / "trash") == []


def test_legacy_audio_qualifies_but_narration_only_and_nested_reviews_do_not(tmp_path):
    root = tmp_path / "generated"
    folder, audio = make_case(root, "legacy", 9 * DAY, legacy=True)
    assert retention._candidate(folder)[0] == NOW - 9 * DAY
    old_audio = folder / "audio" / "part-001.mp3"
    old_audio.parent.mkdir()
    audio.rename(old_audio)
    assert retention._candidate(folder) is not None
    old_audio.unlink()
    assert retention._candidate(folder) is None
    make_case(root / "review", "nested", 9 * DAY)
    assert retention._candidate(root / "review") is None


def test_partial_empty_missing_and_linked_files_are_never_pruned(tmp_path):
    folder, audio = make_case(tmp_path, "case", 9 * DAY)
    partial = folder / "download.partial"
    partial.touch()
    assert retention._candidate(folder) is None
    partial.unlink()
    audio.write_bytes(b"")
    assert retention._candidate(folder) is None
    audio.unlink()
    assert retention._candidate(folder) is None
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"audio")
    audio.symlink_to(outside)
    assert retention._candidate(folder) is None
    link = tmp_path / "linked-case"
    link.symlink_to(folder, target_is_directory=True)
    assert retention._candidate(link) is None


def test_batch_records_completion_and_protects_failed_rerun(tmp_path, monkeypatch):
    monkeypatch.setattr(retention.time, "time", lambda: NOW)
    root = tmp_path / "generated"
    folder, audio = make_case(root, "case", 9 * DAY)
    token = retention.begin_batch(root, ["case", "failed"])
    assert retention._candidate(folder) is None
    with pytest.raises(ValueError, match="already using"):
        retention.begin_batch(root, ["case"])
    retention.complete_case(root, "case", token, [SimpleNamespace(path=audio)])
    retention.finish_batch(root, token)
    assert retention._read_state(folder)["status"] == "complete"
    assert retention._read_state(root / "failed")["status"] == "incomplete"
    assert retention._candidate(folder)[0] == NOW
    # Even with a previous MP3 still present, a failed rerun must not qualify.
    token = retention.begin_batch(root, ["case"])
    retention.finish_batch(root, token)
    assert retention._candidate(folder) is None


def test_completion_rejects_missing_files_and_wrong_run(tmp_path):
    token = retention.begin_batch(tmp_path, ["case"])
    with pytest.raises(ValueError, match="different run"):
        retention.complete_case(tmp_path, "case", "wrong-token", [])
    with pytest.raises(ValueError, match="missing or empty"):
        retention.complete_case(
            tmp_path, "case", token, [SimpleNamespace(path=tmp_path / "case" / "missing.mp3")]
        )


def test_legacy_multipart_requires_every_part(tmp_path):
    folder, audio = make_case(tmp_path, "long", 9 * DAY, legacy=True)
    (folder / "narration.txt").write_text("Case prose. " * 10000)
    audio.rename(audio.with_name("longCase-part-001.mp3"))
    assert retention._candidate(folder) is None
    audio.with_name("longCase-part-002.mp3").write_bytes(b"second part")
    assert retention._candidate(folder) is not None


def test_malformed_state_is_preserved(tmp_path):
    folder, _ = make_case(tmp_path, "case", 9 * DAY)
    (folder / retention.STATE_FILE).write_text("broken json")
    assert retention._candidate(folder) is None
