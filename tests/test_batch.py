from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from case2audio import auth, cli, extractor, polly
from case2audio.errors import Case2AudioError
from case2audio.quality import ExtractionSignals, assess_narration


@pytest.fixture
def batch(tmp_path, monkeypatch):
    pdfs = [tmp_path / "bb.pdf", tmp_path / "sfn.pdf"]
    for pdf in pdfs:
        pdf.touch()
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
            *(str(pdf) for pdf in pdfs),
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
        pdfs=pdfs,
        login=login,
        session=session,
        extract=extract,
        write=write,
        synthesize=synthesize,
    )


def test_batch_preserves_order_and_separate_outputs_with_one_login(batch, capsys):
    assert cli._handle_make(batch.args) == 0
    batch.login.assert_called_once()
    assert [call.args[0] for call in batch.extract.call_args_list] == batch.pdfs
    calls = {call.args[0]: call for call in batch.synthesize.call_args_list}
    assert set(calls) == {"bb", "sfn"}
    # Both workers must share exactly the same client pair and credential refresh lock.
    assert calls["bb"].kwargs["clients"] is calls["sfn"].kwargs["clients"]
    assert batch.session.client.call_count == 2
    for pdf, write in zip(batch.pdfs, batch.write.call_args_list, strict=True):
        synth = calls[pdf.stem]
        assert write.args[1] == batch.args.output_dir / pdf.stem / "narration.txt"
        assert synth.args[1] == batch.args.output_dir / pdf.stem / f"{pdf.stem} Case"
        assert "session" not in synth.kwargs
        assert synth.kwargs["label"] == pdf.name
    assert "Processing PDF 2/2: sfn.pdf" in capsys.readouterr().out


@pytest.mark.parametrize("invalid", ["missing", "duplicate", "extension"])
def test_batch_checks_all_paths_before_login_or_synthesis(batch, invalid):
    if invalid == "missing":
        batch.args.pdfs[1] = batch.pdfs[1].with_name("missing.pdf")
    elif invalid == "duplicate":
        batch.args.pdfs[1] = batch.pdfs[0]
    else:
        invalid_path = batch.pdfs[1].with_suffix(".txt")
        invalid_path.touch()
        batch.args.pdfs[1] = invalid_path
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


def test_failure_still_collects_other_running_job_and_skips_remaining(batch, capsys):
    third = batch.pdfs[0].with_name("third.pdf")
    third.touch()
    batch.args.pdfs.append(third)
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


def test_invalid_worker_limit_fails_before_aws(batch):
    batch.args.jobs = 0
    with pytest.raises(Case2AudioError, match="--jobs must be at least 1"):
        cli._handle_make(batch.args)
    batch.login.assert_not_called()
