from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from case2audio import auth, cli, extractor, polly, retention
from case2audio.errors import Case2AudioError
from case2audio.quality import ExtractionSignals, assess_narration


@pytest.fixture
def batch(tmp_path, monkeypatch):
    inputs = [tmp_path / "bb.pdf", tmp_path / "sfn.pdf"]
    for path in inputs:
        path.touch()
    session = Mock()
    login = Mock(return_value=session)
    monkeypatch.setattr(auth, "prepare_session", login)
    monkeypatch.setattr(polly, "_validate_voice_engine", Mock())
    report = assess_narration("Case text.", signals=ExtractionSignals())
    extract = Mock(
        side_effect=lambda pdf, **_: SimpleNamespace(
            narration=pdf.stem,
            quality_report=report,
        )
    )
    write = Mock()
    synthesize = Mock(return_value=[])
    # Replace every extraction/AWS boundary: batch tests cannot load models or submit paid jobs.
    monkeypatch.setattr(extractor, "extract_pdf", extract)
    monkeypatch.setattr(extractor, "write_extraction", write)
    monkeypatch.setattr(polly, "synthesize_to_directory", synthesize)
    args = cli.build_parser().parse_args(
        [
            "make",
            *(str(path) for path in inputs),
            "--bucket",
            "example",
            "--profile",
            "case2audio",
            "--no-ocr",
            "--output-dir",
            str(tmp_path / "generated"),
        ]
    )
    return SimpleNamespace(
        args=args,
        inputs=inputs,
        login=login,
        session=session,
        extract=extract,
        write=write,
        synthesize=synthesize,
    )


def test_batch_preserves_order_and_separate_outputs_with_one_login(batch, capsys):
    assert cli._handle_make(batch.args) == 0
    batch.login.assert_called_once()
    assert [call.args[0] for call in batch.extract.call_args_list] == batch.inputs
    calls = {call.args[0]: call for call in batch.synthesize.call_args_list}
    assert set(calls) == {"bb", "sfn"}
    # Both workers must share exactly the same client pair and credential refresh lock.
    assert calls["bb"].kwargs["clients"] is calls["sfn"].kwargs["clients"]
    assert batch.session.client.call_count == 2
    for call in batch.session.client.call_args_list:
        assert call.kwargs["config"].max_pool_connections == batch.args.jobs
        assert call.kwargs["config"].retries["mode"] == "standard"
    for path, write in zip(batch.inputs, batch.write.call_args_list, strict=True):
        synth = calls[path.stem]
        assert write.args[1] == batch.args.output_dir / path.stem / "narration.txt"
        assert synth.args[1] == batch.args.output_dir / path.stem / f"{path.stem} Case"
        assert "session" not in synth.kwargs
        assert synth.kwargs["label"] == path.name
    assert "Processing input 2/2: sfn.pdf" in capsys.readouterr().out


def test_mixed_batch_preserves_script_without_invoking_pdf_extraction(batch, capsys):
    pdf, _ = batch.inputs
    text_file = pdf.with_name("handwritten-case.TXT")
    # These resemble PDF furniture and citations, but an authored script owns its wording.
    script = "References\n\nOriginal narration (Smith et al., 2024).\n\nCopyright 2024.\n"
    text_file.write_text(script, encoding="utf-8")
    batch.inputs = [pdf, text_file]
    batch.args.inputs = list(batch.inputs)

    assert cli._handle_make(batch.args) == 0

    assert [call.args[0] for call in batch.extract.call_args_list] == [pdf]
    assert (batch.args.output_dir / text_file.stem / "narration.txt").read_text() == script
    text_call = next(
        call
        for call in batch.synthesize.call_args_list
        if call.args[1].parent.name == text_file.stem
    )
    assert text_call.args[0] == script
    assert text_call.args[1] == (
        batch.args.output_dir / text_file.stem / f"{text_file.stem} Case"
    )
    assert "Processing input 2/2: handwritten-case.TXT" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("filename", "contents", "error"),
    [
        ("missing.txt", None, "Input not found"),
        ("empty.txt", b"", "quality gate"),
        ("corrupt.txt", b"123 234 345 " * 100, "quality gate"),
        ("undecodable.txt", b"\xff\xfe", "read|decode|UTF-8"),
    ],
)
def test_invalid_text_batch_item_never_reaches_synthesis(batch, filename, contents, error):
    text_file = batch.inputs[0].with_name(filename)
    if contents is not None:
        text_file.write_bytes(contents)
    batch.args.inputs[0] = text_file

    with pytest.raises(Case2AudioError, match=error):
        cli._handle_make(batch.args)

    batch.synthesize.assert_not_called()


@pytest.mark.parametrize("invalid", ["missing", "duplicate", "extension"])
def test_batch_checks_all_paths_before_login_or_synthesis(batch, invalid):
    if invalid == "missing":
        batch.args.inputs[1] = batch.inputs[1].with_name("missing.pdf")
    elif invalid == "duplicate":
        duplicate = batch.inputs[0].with_suffix(".txt")
        duplicate.touch()
        batch.args.inputs[1] = duplicate
    else:
        invalid_path = batch.inputs[1].with_suffix(".docx")
        invalid_path.touch()
        batch.args.inputs[1] = invalid_path
    with pytest.raises(Case2AudioError):
        cli._handle_make(batch.args)
    batch.login.assert_not_called()
    batch.extract.assert_not_called()
    batch.synthesize.assert_not_called()


def test_batch_stops_after_first_failed_pdf(batch):
    batch.args.jobs = 1
    batch.synthesize.side_effect = Case2AudioError("Polly failed")
    with pytest.raises(Case2AudioError, match="bb.pdf: Polly failed"):
        cli._handle_make(batch.args)
    assert batch.extract.call_count == 1
    assert batch.synthesize.call_count == 1


def test_polly_jobs_overlap(batch):
    both_started = Barrier(2)

    def synthesize(*args, **kwargs):
        # A sequential implementation times out: both workers must enter before either can finish.
        both_started.wait(timeout=3)
        return []

    batch.synthesize.side_effect = synthesize
    assert cli._handle_make(batch.args) == 0
    assert batch.synthesize.call_count == 2


def test_default_limit_allows_five_jobs_to_overlap(batch):
    for name in ("third.pdf", "fourth.pdf", "fifth.pdf"):
        path = batch.inputs[0].with_name(name)
        path.touch()
        batch.args.inputs.append(path)
    all_started = Barrier(5)

    def synthesize(*_args, **_kwargs):
        # A two-job default would deadlock before the fifth worker starts.
        all_started.wait(timeout=3)
        return []

    batch.synthesize.side_effect = synthesize
    assert batch.args.jobs == 20
    assert cli._handle_make(batch.args) == 0
    assert batch.synthesize.call_count == 5
    assert len({call.kwargs["pacer"] for call in batch.synthesize.call_args_list}) == 1


def test_batch_reports_progress_while_jobs_are_still_running(batch, monkeypatch, capsys):
    release = Event()
    real_wait = cli.wait
    reported = False

    def wait_once_without_completion(futures, *, timeout, return_when):
        nonlocal reported
        if timeout == 30 and not reported:
            reported = True
            release.set()
            return set(), set(futures)
        return real_wait(futures, timeout=timeout, return_when=return_when)

    def synthesize(*_args, **_kwargs):
        release.wait(timeout=3)
        return []

    monkeypatch.setattr(cli, "wait", wait_once_without_completion)
    batch.synthesize.side_effect = synthesize
    assert cli._handle_make(batch.args) == 0
    assert reported
    assert "Batch progress: 2 audio job(s) running; 0 input(s) waiting to prepare." in (
        capsys.readouterr().out
    )


def test_failure_still_collects_other_running_job_and_skips_remaining(batch, capsys):
    third = batch.inputs[0].with_name("third.pdf")
    third.touch()
    batch.args.inputs.append(third)
    both_started = Barrier(2)

    def synthesize(text, *args, **kwargs):
        both_started.wait(timeout=3)
        raise Case2AudioError(f"failure in {text}")

    batch.synthesize.side_effect = synthesize
    with pytest.raises(Case2AudioError) as error:
        cli._handle_make(batch.args)
    assert "failure in bb" in str(error.value)
    assert "failure in sfn" in str(error.value)
    assert batch.extract.call_count == 2


def test_successful_running_job_is_downloaded_after_another_fails(batch, capsys):
    both_started = Barrier(2)

    def synthesize(text, *args, **kwargs):
        both_started.wait(timeout=3)
        if text == "bb":
            raise Case2AudioError("Polly failed")
        return []

    batch.synthesize.side_effect = synthesize
    with pytest.raises(Case2AudioError, match="bb.pdf: Polly failed"):
        cli._handle_make(batch.args)
    assert "Audio ready: sfn.pdf" in capsys.readouterr().out


def test_local_failure_explains_that_an_earlier_polly_task_is_still_finishing(batch, capsys):
    report = assess_narration("Case text.", signals=ExtractionSignals())
    release_first_task = Event()

    def fail_second_extraction(*_args, **_kwargs):
        # Keep the first paid task pending until the second PDF reaches its local gate.
        release_first_task.set()
        raise Case2AudioError("Narration failed the pre-Polly quality gate")

    def finish_after_local_failure(*_args, **_kwargs):
        release_first_task.wait(timeout=3)
        return []

    def extract_or_fail(pdf, **_kwargs):
        if pdf.name == "bb.pdf":
            return SimpleNamespace(narration="bb", quality_report=report)
        return fail_second_extraction()

    batch.extract.side_effect = extract_or_fail
    batch.synthesize.side_effect = finish_after_local_failure

    with pytest.raises(Case2AudioError, match="sfn.pdf"):
        cli._handle_make(batch.args)

    assert "Local processing stopped; waiting for 1 already-submitted Polly task(s)" in (
        capsys.readouterr().out
    )


@pytest.mark.parametrize("invalid", [0, 21])
def test_invalid_worker_limit_fails_before_aws(batch, invalid):
    batch.args.jobs = invalid
    with pytest.raises(Case2AudioError, match="--jobs must be between 1 and 20"):
        cli._handle_make(batch.args)
    batch.login.assert_not_called()


@pytest.mark.parametrize("fails", [False, True])
def test_cleanup_runs_after_submission_even_if_downloads_fail(batch, monkeypatch, fails):
    batch.args.jobs = 1
    started = Event()
    original_extract = batch.extract.side_effect

    def extract(pdf, **kwargs):
        if not started.is_set():
            cleanup.assert_not_called()
        return original_extract(pdf, **kwargs)

    def check_start(root):
        assert started.is_set()
        # The initial check protects the entire batch, including inputs not yet prepared.
        if cleanup.call_count == 1:
            for path in batch.inputs:
                assert retention._read_state(root / path.stem)["status"] == "running"

    cleanup = Mock(side_effect=check_start)
    monkeypatch.setattr(retention, "prune_cases", cleanup)
    batch.extract.side_effect = extract

    def downloaded(text, audio_dir, *args, **kwargs):
        started.set()
        kwargs["on_started"]()
        if fails:
            raise Case2AudioError("Polly failed")
        audio_dir.mkdir(parents=True, exist_ok=True)
        path = audio_dir / f"{text}Case.mp3"
        path.write_bytes(b"downloaded audio")
        return [SimpleNamespace(path=path)]

    batch.synthesize.side_effect = downloaded
    if fails:
        with pytest.raises(Case2AudioError):
            cli._handle_make(batch.args)
        cleanup.assert_called_once_with(batch.args.output_dir)
    else:
        assert cli._handle_make(batch.args) == 0
        # Once after the first submission, then once after each successful download.
        assert cleanup.call_count == 3
        assert all(call.args == (batch.args.output_dir,) for call in cleanup.call_args_list)
        for path in batch.inputs:
            assert retention._read_state(batch.args.output_dir / path.stem)["status"] == "complete"


def test_cleanup_skipped_when_first_input_fails_before_submission(batch, monkeypatch):
    cleanup = Mock()
    monkeypatch.setattr(retention, "prune_cases", cleanup)
    batch.extract.side_effect = Case2AudioError("bad extraction")
    with pytest.raises(Case2AudioError, match="bad extraction"):
        cli._handle_make(batch.args)
    cleanup.assert_not_called()


def test_cleanup_skipped_when_polly_rejects_submission(batch, monkeypatch):
    cleanup = Mock()
    monkeypatch.setattr(retention, "prune_cases", cleanup)
    batch.synthesize.side_effect = Case2AudioError("submission rejected")
    with pytest.raises(Case2AudioError, match="submission rejected"):
        cli._handle_make(batch.args)
    cleanup.assert_not_called()


def test_concurrent_submissions_run_initial_cleanup_once(batch, monkeypatch):
    both_started = Barrier(2)
    cleanup = Mock()
    monkeypatch.setattr(retention, "prune_cases", cleanup)

    def synthesize(*_args, **kwargs):
        both_started.wait(timeout=3)
        kwargs["on_started"]()
        return []

    batch.synthesize.side_effect = synthesize
    assert cli._handle_make(batch.args) == 0
    # One initial check plus the existing check after each successful worker result.
    assert cleanup.call_count == 3


def test_cleanup_failure_does_not_stop_audio_workers(batch, monkeypatch, capsys):
    cleanup = Mock(side_effect=OSError("Trash unavailable"))
    monkeypatch.setattr(retention, "prune_cases", cleanup)

    def synthesize(*_args, **kwargs):
        kwargs["on_started"]()
        return []

    batch.synthesize.side_effect = synthesize
    assert cli._handle_make(batch.args) == 0
    assert batch.synthesize.call_count == 2
    assert "Local cleanup skipped: Trash unavailable" in capsys.readouterr().err
