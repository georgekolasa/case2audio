"""Amazon Polly long-form task orchestration."""

from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
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
    # Long generative tasks need more time; each part gets its own deadline.
    timeout_seconds: float = 25 * 60.0


@dataclass(frozen=True)
class AudioPart:
    """One task and local output; output_uri is historical when s3_deleted is true."""

    task_id: str
    output_uri: str
    path: Path
    s3_deleted: bool = False


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
        named_audio_key(options, label, index, len(chunks)) for index in range(1, len(chunks) + 1)
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
        # Count the submitted part so split PDFs report the work for this specific task.
        _print_progress(
            f"Polly is processing part {index}/{len(chunks)} "
            f"({len(chunk):,} characters; task {task_id}).",
            label,
        )
        task = _wait_for_task(
            polly,
            task_id,
            poll_seconds=options.poll_seconds,
            timeout_seconds=options.timeout_seconds,
        )
        output_uri = task["OutputUri"]
        extension = "mp3" if options.output_format == "mp3" else options.output_format
        # Named downloads remain recognizable when copied out of their case folder.
        if label:
            suffix = f"-part-{index:03d}" if len(chunks) > 1 else ""
            filename = f"{Path(label).stem}Case{suffix}.{extension}"
        else:
            filename = f"part-{index:03d}.{extension}"
        part_path = output_dir / filename
        key = s3_key_from_output_uri(output_uri, options.bucket)
        _print_progress(f"Polly finished part {index}/{len(chunks)}; downloading audio...", label)
        try:
            source = _download_verified(s3, options.bucket, key, part_path)
        except Exception as exc:
            raise Case2AudioError(
                f"Could not verify download from s3://{options.bucket}/{key}: {exc}. "
                "S3 audio retained; recover it before lifecycle expiry. Do not rerun synthesis."
            ) from exc

        named_key = named_keys[index - 1]
        copies = [(key, source)]
        try:
            # Pin the source so another run cannot change the object between download and copy.
            copy_source = {"Bucket": options.bucket, "Key": key}
            if source.get("VersionId"):
                copy_source["VersionId"] = source["VersionId"]
            copied = s3.copy_object(
                Bucket=options.bucket,
                Key=named_key,
                CopySource=copy_source,
                CopySourceIfMatch=source["ETag"],
            )
            copies.append(
                (
                    named_key,
                    {
                        "ETag": copied["CopyObjectResult"]["ETag"],
                        "VersionId": copied.get("VersionId"),
                    },
                )
            )
        except Exception as exc:
            # Local audio is already safe; a naming failure must not invite paid resynthesis.
            _print_progress(
                f"WARNING: Audio verified locally, but S3 naming failed: {exc}. "
                "S3 audio retained for lifecycle cleanup; do not rerun synthesis.",
                label,
            )
            parts.append(AudioPart(task_id, output_uri, part_path))
            continue
        output_uri = f"s3://{options.bucket}/{named_key}"
        deleted = _delete_verified_copies(s3, options.bucket, copies, label)
        parts.append(AudioPart(task_id, output_uri, part_path, s3_deleted=deleted))

    return parts


def _download_verified(s3: Any, bucket: str, key: str, destination: Path) -> dict:
    """Stream once, verify size and persisted SHA-256, then atomically publish the file."""

    # The SDK validates S3 checksums when supplied; read through EOF so validation completes.
    response = s3.get_object(Bucket=bucket, Key=key, ChecksumMode="ENABLED")
    temporary: Path | None = None
    try:
        expected_size = response["ContentLength"]
        if expected_size <= 0 or not response.get("ETag"):
            raise ValueError("S3 returned empty audio or missing object identity")
        received_hash = hashlib.sha256()
        received_size = 0
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, suffix=".partial", delete=False
        ) as f:
            temporary = Path(f.name)
            while data := response["Body"].read(1024 * 1024):
                f.write(data)
                received_hash.update(data)
                received_size += len(data)
            f.flush()
            os.fsync(f.fileno())  # Flush file contents before removing the remote recovery copy.
        if received_size != expected_size:
            raise ValueError(f"Expected {expected_size} bytes, received {received_size}")
        disk_hash = hashlib.sha256()
        with temporary.open("rb") as f:
            while data := f.read(1024 * 1024):
                disk_hash.update(data)
        if (
            temporary.stat().st_size != expected_size
            or disk_hash.digest() != received_hash.digest()
        ):
            raise ValueError("Downloaded audio did not pass the on-disk checksum check")
        temporary.replace(destination)  # Failed downloads never overwrite an existing good MP3.
        return {"ETag": response["ETag"], "VersionId": response.get("VersionId")}
    finally:
        response["Body"].close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _delete_verified_copies(s3: Any, bucket: str, copies: list, label: str | None) -> bool:
    deleted = True
    for key, identity in reversed(copies):
        try:
            args = {"Bucket": bucket, "Key": key, "IfMatch": identity["ETag"]}
            if identity.get("VersionId"):
                # Delete the downloaded version, not just a marker that leaves storage billed.
                args["VersionId"] = identity["VersionId"]
            s3.delete_object(**args)
        except Exception as exc:
            deleted = False
            _print_progress(
                f"WARNING: Local audio verified, but S3 cleanup failed for s3://{bucket}/{key}: "
                f"{exc}. Lifecycle cleanup is the fallback; do not rerun synthesis.",
                label,
            )
    if deleted:
        _print_progress("Local audio verified; deleted both S3 copies.", label)
    return deleted


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

    stamp = datetime.now().astimezone().strftime("%H:%M:%S %Z")
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
