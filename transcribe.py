#!/usr/bin/env python3
"""
transcribe.py -- local interface to Amazon Transcribe

Usage:
  python transcribe.py meeting.m4a --bucket my-bucket

Uploads the audio to S3, starts a Transcribe job, waits for it, and writes the
plaintext transcript to stdout. Progress goes to stderr.

"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

KNOWN_FORMATS = {
    ".amr": "amr", ".flac": "flac", ".m4a": "m4a", ".mp3": "mp3",
    ".mp4": "mp4", ".ogg": "ogg", ".wav": "wav", ".webm": "webm",
}

POLL_START = 5.0
POLL_CEILING = 30.0


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)

def build_session(profile: str | None, region: str) -> boto3.Session:
    """One session for every privileged call, bound to AWS_PROFILE if set."""
    if profile:
        log(f"aws profile: {profile}")
    else:
        log("AWS_PROFILE not set -- falling back to the default credential chain")
    return boto3.Session(profile_name=profile, region_name=region)

def make_job_name(media: Path) -> str:
    """Transcribe job names allow [0-9a-zA-Z._-] only, and must be unique."""
    stem = re.sub(r"[^0-9a-zA-Z._-]", "-", media.stem)[:150].strip("-") or "audio"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{stem}-{stamp}-{uuid.uuid4().hex[:6]}"

def render_speakers(results: dict) -> str | None:
    """Turn speaker-labelled results into 'spk_0: ...' lines, one per turn."""
    labels = results.get("speaker_labels")
    if not labels:
        return None

    # segments[].items[] tag each spoken word with a speaker, keyed by start time.
    speaker_at = {
        item["start_time"]: item["speaker_label"]
        for segment in labels.get("segments", [])
        for item in segment.get("items", [])
    }

    lines: list[str] = []
    current: str | None = None
    words: list[str] = []

    for item in results.get("items", []):
        content = item["alternatives"][0]["content"]
        if item.get("type") == "punctuation":
            if words:
                words[-1] += content
            continue
        speaker = speaker_at.get(item.get("start_time"), current or "spk_0")
        if speaker != current:
            if words:
                lines.append(f"{current}: {' '.join(words)}")
            current, words = speaker, []
        words.append(content)

    if words:
        lines.append(f"{current}: {' '.join(words)}")
    return "\n".join(lines)

def wait_for_job(transcribe, job_name: str, timeout: int) -> dict:
    """Poll with backoff until the job settles, or give up after `timeout`."""
    deadline = time.monotonic() + timeout
    delay = POLL_START
    last_status = None

    while True:
        job = transcribe.get_transcription_job(
            TranscriptionJobName=job_name
        )["TranscriptionJob"]
        status = job["TranscriptionJobStatus"]

        if status != last_status:
            log(f"  {status.lower()}")
            last_status = status
        if status in ("COMPLETED", "FAILED"):
            return job
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"gave up after {timeout}s; job {job_name} is still {status}. "
                f"It keeps running -- check it with "
                f"`aws transcribe get-transcription-job "
                f"--transcription-job-name {job_name}`"
            )

        time.sleep(min(delay, max(0.0, deadline - time.monotonic())))
        delay = min(delay * 1.5, POLL_CEILING)

def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Transcribe a local audio file with Amazon Transcribe.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("audio", help="path to the local audio/video file")
    p.add_argument(
        "-b", "--bucket",
        default=os.environ.get("TRANSCRIBE_BUCKET"),
        help="S3 bucket for the upload and the transcript "
             "(default: $TRANSCRIBE_BUCKET)",
    )
    p.add_argument("--prefix", default="transcribe",
                   help="key prefix inside the bucket")
    p.add_argument("--profile", default=os.environ.get("AWS_PROFILE"),
                   help="AWS profile (default: $AWS_PROFILE)")
    p.add_argument("--region", default="ca-central-1",
                   help="override the region (default ca-central-1)")
    p.add_argument("-l", "--language", default="en-US",
                   help="BCP-47 language code, or 'auto' to let Transcribe detect it")
    p.add_argument("-s", "--speakers", type=int, metavar="N", nargs="?", const=2,
                   help="enable speaker diarization, expecting up to N speakers")
    p.add_argument("-o", "--output", metavar="PATH",
                   help="write the transcript here instead of stdout")
    p.add_argument("--save-json", metavar="PATH",
                   help="also save the raw Transcribe JSON here")
    p.add_argument("--keep-media", action="store_true",
                   help="leave the uploaded audio in S3 (default: delete it)")
    p.add_argument("--timeout", type=int, default=3600,
                   help="seconds to wait for the job")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    media = Path(args.audio).expanduser()
    if not media.is_file():
        log(f"error: no such file: {media}")
        return 2
    if not args.bucket:
        log("error: no bucket. Pass --bucket or set $TRANSCRIBE_BUCKET.")
        return 2

    session = build_session(args.profile, args.region)
    s3 = session.client("s3")
    transcribe = session.client("transcribe")

    # Not required, but it makes the target account obvious before we spend money.
    try:
        who = session.client("sts").get_caller_identity()
        log(f"account {who['Account']} as {who['Arn'].rsplit('/', 1)[-1]}")
    except (ClientError, BotoCoreError):
        pass  # sts:GetCallerIdentity may be denied; not worth failing over

    job_name = make_job_name(media)
    suffix = media.suffix.lower()
    media_key = f"{args.prefix}/media/{job_name}{suffix}"
    output_key = f"{args.prefix}/transcripts/{job_name}.json"

    job_args = {
        "TranscriptionJobName": job_name,
        "Media": {"MediaFileUri": f"s3://{args.bucket}/{media_key}"},
        # Keep the JSON in the caller's own bucket rather than a service-managed
        # one, so the data never leaves this account and needs no presigned URL.
        "OutputBucketName": args.bucket,
        "OutputKey": output_key,
    }
    if args.language == "auto":
        job_args["IdentifyLanguage"] = True
    else:
        job_args["LanguageCode"] = args.language
    if suffix in KNOWN_FORMATS:
        job_args["MediaFormat"] = KNOWN_FORMATS[suffix]
    if args.speakers:
        job_args["Settings"] = {
            "ShowSpeakerLabels": True,
            "MaxSpeakerLabels": args.speakers,
        }

    uploaded = False
    try:
        size_mb = media.stat().st_size / 1e6
        log(f"uploading {media.name} ({size_mb:.1f} MB) -> s3://{args.bucket}/{media_key}")
        s3.upload_file(str(media), args.bucket, media_key)
        uploaded = True

        log(f"starting job {job_name}")
        transcribe.start_transcription_job(**job_args)

        job = wait_for_job(transcribe, job_name, args.timeout)
        if job["TranscriptionJobStatus"] == "FAILED":
            log(f"error: job failed: {job.get('FailureReason', 'no reason given')}")
            return 1

        body = s3.get_object(Bucket=args.bucket, Key=output_key)["Body"].read()
        payload = json.loads(body)
        results = payload["results"]

        if detected := payload.get("results", {}) and job.get("LanguageCode"):
            log(f"language: {detected}")

        text = render_speakers(results) or results["transcripts"][0]["transcript"]

        if args.save_json:
            Path(args.save_json).expanduser().write_bytes(body)
            log(f"raw json -> {args.save_json}")
        if args.output:
            Path(args.output).expanduser().write_text(text + "\n", encoding="utf-8")
            log(f"transcript -> {args.output}")
        else:
            print(text)

        log(f"transcript kept at s3://{args.bucket}/{output_key}")
        return 0

    except KeyboardInterrupt:
        log(f"\ninterrupted. Job {job_name} may still be running in AWS.")
        return 130
    except TimeoutError as exc:
        log(f"error: {exc}")
        return 1
    except (ClientError, BotoCoreError) as exc:
        log(f"error: {exc}")
        return 1
    finally:
        if uploaded and not args.keep_media:
            try:
                s3.delete_object(Bucket=args.bucket, Key=media_key)
                log("removed the uploaded audio")
            except (ClientError, BotoCoreError) as exc:
                log(f"warning: could not delete {media_key}: {exc}")


if __name__ == "__main__":
    sys.exit(main())
