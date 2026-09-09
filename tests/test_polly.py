import re
from pathlib import Path
from unittest.mock import Mock

from case2audio.polly import (
    PollyOptions,
    named_audio_key,
    s3_key_from_output_uri,
    split_for_polly,
    synthesize_to_directory,
)


def test_split_for_polly_respects_limit_and_order() -> None:
    text = "First paragraph.\n\n" + ("word " * 60) + "\n\nLast paragraph."

    chunks = split_for_polly(text, max_chars=100)

    assert len(chunks) > 2
    assert all(len(chunk) <= 100 for chunk in chunks)
    assert " ".join(chunks).startswith("First paragraph.")
    assert chunks[-1].endswith("Last paragraph.")


def test_s3_key_supports_both_aws_url_styles() -> None:
    assert (
        s3_key_from_output_uri(
            "https://s3.us-east-2.amazonaws.com/example-bucket/jobs/a.123.mp3",
            "example-bucket",
        )
        == "jobs/a.123.mp3"
    )
    assert (
        s3_key_from_output_uri(
            "https://example-bucket.s3.us-east-2.amazonaws.com/jobs/a.123.mp3",
            "example-bucket",
        )
        == "jobs/a.123.mp3"
    )


class FakePolly:
    def __init__(self) -> None:
        self.start_calls = []

    def start_speech_synthesis_task(self, **kwargs):
        self.start_calls.append(kwargs)
        return {"SynthesisTask": {"TaskId": "task-123"}}

    def describe_voices(self, **kwargs):
        assert kwargs == {"Engine": "generative"}
        return {"Voices": [{"Id": "Matthew"}]}

    def get_speech_synthesis_task(self, **kwargs):
        assert kwargs == {"TaskId": "task-123"}
        return {
            "SynthesisTask": {
                "TaskStatus": "completed",
                "OutputUri": "https://example.s3.us-east-2.amazonaws.com/job/task-123.mp3",
            }
        }


class FakeS3:
    def __init__(self) -> None:
        self.download_calls = []
        self.copy_calls = []

    def copy_object(self, **kwargs):
        self.copy_calls.append(kwargs)

    def download_file(self, bucket, key, filename):
        self.download_calls.append((bucket, key, filename))
        # The fake writes bytes so callers can verify a real local output was made.
        Path(filename).write_bytes(b"fake mp3")


class FakeSession:
    def __init__(self) -> None:
        self.polly = FakePolly()
        self.s3 = FakeS3()
        self.client_calls = []

    def client(self, name, **kwargs):
        self.client_calls.append((name, kwargs))
        return self.polly if name == "polly" else self.s3


def test_service_region_does_not_override_login_region(tmp_path, monkeypatch):
    import boto3

    session = FakeSession()
    factory = Mock(return_value=session)
    monkeypatch.setattr(boto3, "Session", factory)
    synthesize_to_directory(
        "A short case.",
        tmp_path,
        PollyOptions(bucket="example", profile="case2audio", region="us-east-1"),
    )
    factory.assert_called_once_with(profile_name="case2audio")
    assert session.client_calls == [
        ("polly", {"region_name": "us-east-1"}),
        ("s3", {"region_name": "us-east-1"}),
    ]


def test_shared_clients_do_not_create_a_second_credential_session(tmp_path, monkeypatch):
    import boto3

    factory = Mock(side_effect=AssertionError("Worker must reuse the prepared clients"))
    monkeypatch.setattr(boto3, "Session", factory)
    session = FakeSession()
    parts = synthesize_to_directory(
        "A short case.",
        tmp_path,
        PollyOptions(bucket="example"),
        clients=(session.polly, session.s3),
    )
    assert parts[0].path.read_bytes() == b"fake mp3"
    factory.assert_not_called()


def test_synthesis_submits_waits_and_downloads(tmp_path: Path, capsys) -> None:
    session = FakeSession()

    parts = synthesize_to_directory(
        "A short case.",
        tmp_path,
        PollyOptions(bucket="example", poll_seconds=0, timeout_seconds=1),
        session=session,
        label="bb.pdf",
    )

    assert len(parts) == 1
    assert parts[0].path.read_bytes() == b"fake mp3"
    assert session.polly.start_calls[0]["Text"] == "A short case."
    assert session.s3.download_calls[0][1] == "job/task-123.mp3"
    assert session.s3.copy_calls == [
        {
            "Bucket": "example",
            "Key": "case2audio/bb.mp3",
            "CopySource": {"Bucket": "example", "Key": "job/task-123.mp3"},
        }
    ]
    assert parts[0].output_uri == "s3://example/case2audio/bb.mp3"
    assert parts[0].path == tmp_path / "bbCase.mp3"
    progress = capsys.readouterr().out
    assert "Connecting to Amazon Polly (Matthew, generative)" in progress
    assert "Submitting 1 audio part to Polly" in progress
    assert "Polly is processing part 1/1 (13 characters; task task-123)" in progress
    assert "Polly finished part 1/1; downloading audio" in progress
    assert re.search(
        r"\[\d{2}:\d{2}:\d{2} [^\]]+\] \[bb.pdf\] Polly is processing",
        progress,
    )


def test_synthesis_rejects_unsupported_voice_before_starting_task(tmp_path: Path) -> None:
    session = FakeSession()
    session.polly.describe_voices = lambda **kwargs: {"Voices": []}

    try:
        synthesize_to_directory(
            "A short case.",
            tmp_path,
            PollyOptions(bucket="example", region="us-east-2"),
            session=session,
        )
    except Exception as exc:
        assert "Matthew + generative is available in us-east-1" in str(exc)
    else:
        raise AssertionError("Expected unsupported voice settings to fail")

    assert session.polly.start_calls == []


def test_large_document_copies_each_part_to_a_distinct_name(tmp_path):
    session = FakeSession()
    parts = synthesize_to_directory(
        "word " * 20_000,
        tmp_path,
        PollyOptions(bucket="example"),
        session=session,
        label="bb.pdf",
    )
    assert len(parts) == 2
    assert [part.path.name for part in parts] == ["bbCase-part-001.mp3", "bbCase-part-002.mp3"]
    assert [call["Key"] for call in session.s3.copy_calls] == [
        "case2audio/bb-part-001.mp3",
        "case2audio/bb-part-002.mp3",
    ]


def test_readable_key_preserves_spaces_and_dots():
    options = PollyOptions(bucket="example", prefix="custom/")
    assert named_audio_key(options, "My case.v2.pdf", 1, 1) == "custom/My case.v2.mp3"
    assert named_audio_key(PollyOptions(bucket="example", prefix=""), "bb.pdf", 1, 1) == "bb.mp3"


def test_copy_failure_keeps_download_and_does_not_resynthesize(tmp_path):
    import pytest

    from case2audio.errors import Case2AudioError

    session = FakeSession()
    session.s3.copy_object = Mock(side_effect=RuntimeError("copy denied"))
    with pytest.raises(Case2AudioError, match="do not rerun synthesis"):
        synthesize_to_directory(
            "A short case.",
            tmp_path,
            PollyOptions(bucket="example"),
            session=session,
            label="bb.pdf",
        )
    assert (tmp_path / "bbCase.mp3").read_bytes() == b"fake mp3"
    assert len(session.polly.start_calls) == 1
