from pathlib import Path

from case2audio.polly import (
    PollyOptions,
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

    def download_file(self, bucket, key, filename):
        self.download_calls.append((bucket, key, filename))
        # The fake writes bytes so callers can verify a real local output was made.
        Path(filename).write_bytes(b"fake mp3")


class FakeSession:
    def __init__(self) -> None:
        self.polly = FakePolly()
        self.s3 = FakeS3()

    def client(self, name):
        return self.polly if name == "polly" else self.s3


def test_synthesis_submits_waits_and_downloads(tmp_path: Path) -> None:
    session = FakeSession()

    parts = synthesize_to_directory(
        "A short case.",
        tmp_path,
        PollyOptions(bucket="example", poll_seconds=0, timeout_seconds=1),
        session=session,
    )

    assert len(parts) == 1
    assert parts[0].path.read_bytes() == b"fake mp3"
    assert session.polly.start_calls[0]["Text"] == "A short case."
    assert session.s3.download_calls[0][1] == "job/task-123.mp3"


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
