"""Amazon Polly long-form task orchestration."""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .errors import Case2AudioError

# Leave headroom under Polly's 100,000 billed-character asynchronous limit.
DEFAULT_CHUNK_SIZE = 95_000


@dataclass(frozen=True)
class PollyOptions:
    """AWS choices needed for repeatable synthesis."""

    bucket: str
    region: str | None = None
    profile: str | None = None
    prefix: str = "case2audio"
    voice: str = "Matthew"
    engine: str = "generative"
    output_format: str = "mp3"
    poll_seconds: float = 5.0
    timeout_seconds: float = 900.0


@dataclass(frozen=True)
class AudioPart:
    """One Polly task and its downloaded local output."""

    task_id: str
    output_uri: str
    path: Path


def split_for_polly(text: str, max_chars: int = DEFAULT_CHUNK_SIZE) -> list[str]:
    """Split at paragraphs, then sentences, with a hard fallback for giant blocks."""

    if max_chars < 100:
        raise ValueError("max_chars must be at least 100")

    paragraphs = [part.strip() for part in re.split(r"\n{2,}", text) if part.strip()]
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        for piece in _split_oversized(paragraph, max_chars):
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= max_chars:
                current = candidate
                continue
            chunks.append(current)
            current = piece

    if current:
        chunks.append(current)
    return chunks


def _split_oversized(text: str, max_chars: int) -> list[str]:
    """Keep sentence boundaries where possible, then fall back to word boundaries."""

    if len(text) <= max_chars:
        return [text]

    sentences = re.split(r"(?<=[.!?])\s+", text)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            pieces.extend(_hard_wrap(sentence, max_chars))
            continue
        candidate = f"{current} {sentence}" if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
        else:
            pieces.append(current)
            current = sentence
    if current:
        pieces.append(current)
    return pieces


def _hard_wrap(text: str, max_chars: int) -> list[str]:
    """Guarantee Polly-safe chunks even when punctuation is missing."""

    words = text.split()
    chunks: list[str] = []
    current = ""
    for word in words:
        # Extremely long tokens are rare URLs; slicing is still safer than rejection.
        if len(word) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(
                word[index : index + max_chars] for index in range(0, len(word), max_chars)
            )
            continue
        candidate = f"{current} {word}" if current else word
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current)
            current = word
    if current:
        chunks.append(current)
    return chunks


def synthesize_to_directory(
    text: str,
    output_dir: Path,
    options: PollyOptions,
    *,
    session: Any | None = None,
    label: str | None = None,
    clients: tuple[Any, Any] | None = None,
) -> list[AudioPart]:
    """Submit, wait for, and download one or more Polly speech tasks."""

    if not text.strip():
        raise Case2AudioError("Narration text is empty; refusing to submit a paid Polly task.")

    if clients is None:
        if session is None:
            import boto3

            # Preserve the login region; select the audio region only on service clients.
            session = boto3.Session(profile_name=options.profile)
        clients = (
            session.client("polly", region_name=options.region),
            session.client("s3", region_name=options.region),
        )

    # AWS can be quiet for minutes, so confirm immediately that the CLI has moved on to Polly.
    _print_progress(f"Connecting to Amazon Polly ({options.voice}, {options.engine})...", label)
    polly, s3 = clients
    _validate_voice_engine(polly, options)
    chunks = split_for_polly(text)
    # Decide readable keys before billing, including separate names for oversized documents.
    named_keys = [
        named_audio_key(options, label, index, len(chunks))
        for index in range(1, len(chunks) + 1)
    ]
    part_word = "part" if len(chunks) == 1 else "parts"
    _print_progress(
        f"Submitting {len(chunks)} audio {part_word} to Polly; this can take several minutes.",
        label,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    parts: list[AudioPart] = []

    for index, chunk in enumerate(chunks, start=1):
        # Polly always appends a task UUID; keep its originals under a recovery folder.
        key_prefix = "/".join(
            part for part in (options.prefix.strip("/"), "_tasks", f"part-{index:03d}") if part
        )
        try:
            response = polly.start_speech_synthesis_task(
                Engine=options.engine,
                OutputFormat=options.output_format,
                OutputS3BucketName=options.bucket,
                OutputS3KeyPrefix=key_prefix,
                Text=chunk,
                TextType="text",
                VoiceId=options.voice,
            )
        except Exception as exc:
            raise Case2AudioError(f"Polly rejected part {index}: {exc}") from exc

        task_id = response["SynthesisTask"]["TaskId"]
        _print_progress(f"Polly is processing part {index}/{len(chunks)} (task {task_id}).", label)
        task = _wait_for_task(
            polly,
            task_id,
            poll_seconds=options.poll_seconds,
            timeout_seconds=options.timeout_seconds,
        )
        output_uri = task["OutputUri"]
        extension = "mp3" if options.output_format == "mp3" else options.output_format
        part_path = output_dir / f"part-{index:03d}.{extension}"
        key = s3_key_from_output_uri(output_uri, options.bucket)
        _print_progress(f"Polly finished part {index}/{len(chunks)}; downloading audio...", label)
        try:
            s3.download_file(options.bucket, key, str(part_path))
        except Exception as exc:
            raise Case2AudioError(f"Could not download s3://{options.bucket}/{key}: {exc}") from exc

        named_key = named_keys[index - 1]
        try:
            # Copy within S3: no new synthesis. The task original remains available for recovery.
            s3.copy_object(
                Bucket=options.bucket,
                Key=named_key,
                CopySource={"Bucket": options.bucket, "Key": key},
            )
        except Exception as exc:
            raise Case2AudioError(
                f"Audio was downloaded to {part_path.resolve()}, but could not copy "
                f"s3://{options.bucket}/{key} to {named_key}: {exc}. "
                "Copy the existing S3 object to retry naming; do not rerun synthesis."
            ) from exc
        output_uri = f"s3://{options.bucket}/{named_key}"
        _print_progress(f"Saved named audio: {output_uri}", label)
        parts.append(AudioPart(task_id=task_id, output_uri=output_uri, path=part_path))

    return parts


def named_audio_key(options: PollyOptions, label: str | None, index: int, total: int) -> str:
    """Use the source filename, with numbered suffixes only when audio spans multiple parts."""

    stem = Path(label).stem if label else "audio"
    suffix = f"-part-{index:03d}" if total > 1 else ""
    key = "/".join(
        part
        for part in (options.prefix.strip("/"), f"{stem}{suffix}.{options.output_format}")
        if part
    )
    # S3 measures keys in UTF-8 bytes, which matters for long non-ASCII PDF filenames.
    if len(key.encode("utf-8")) > 1024:
        raise Case2AudioError("S3 audio filename is too long; shorten the PDF name or --prefix.")
    return key


def _print_progress(message: str, label: str | None = None) -> None:
    """Show local start times and PDF names so overlapping jobs can be followed."""

    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    document = f" [{label}]" if label else ""
    # One write keeps worker messages together when several tasks report at once.
    sys.stdout.write(f"[{stamp}]{document} {message}\n")
    sys.stdout.flush()


def _validate_voice_engine(polly: Any, options: PollyOptions) -> None:
    """Fail before billing when a voice/engine pair is unavailable in the selected region."""

    try:
        voices = polly.describe_voices(Engine=options.engine).get("Voices", [])
    except Exception as exc:
        raise Case2AudioError(f"Could not validate Polly voice settings: {exc}") from exc

    if any(voice.get("Id") == options.voice for voice in voices):
        return

    region = options.region or "the configured AWS region"
    raise Case2AudioError(
        f"Polly voice {options.voice!r} does not support engine {options.engine!r} "
        f"in {region}. Matthew + generative is available in us-east-1."
    )


def _wait_for_task(
    polly: Any,
    task_id: str,
    *,
    poll_seconds: float,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Poll with a deadline so a stuck AWS task cannot hang the CLI forever."""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        task = polly.get_speech_synthesis_task(TaskId=task_id)["SynthesisTask"]
        status = task["TaskStatus"]
        if status == "completed":
            return task
        if status == "failed":
            reason = task.get("TaskStatusReason", "unknown reason")
            raise Case2AudioError(f"Polly task {task_id} failed: {reason}")
        time.sleep(poll_seconds)
    raise Case2AudioError(f"Polly task {task_id} did not finish within {timeout_seconds:g}s")


def s3_key_from_output_uri(output_uri: str, bucket: str) -> str:
    """Handle both path-style and virtual-hosted S3 URLs returned by Polly."""

    parsed = urlparse(output_uri)
    key = unquote(parsed.path.lstrip("/"))
    hostname = parsed.hostname or ""
    if not hostname.startswith(f"{bucket}.s3") and key.startswith(f"{bucket}/"):
        key = key[len(bucket) + 1 :]
    if not key:
        raise Case2AudioError(f"Polly returned an unusable output URI: {output_uri}")
    return key
