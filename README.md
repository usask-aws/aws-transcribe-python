# aws-transcribe-python

Python local interface to AWS Transcribe.

`transcribe.py` takes a local audio or video file, uploads it to your S3
bucket, runs an Amazon Transcribe job, waits for it to finish, and writes the
plaintext transcript to stdout.

The transcript JSON is written to a bucket you control rather than a
service-managed one, so the audio and the text never leave your account. The
uploaded audio is deleted when the job finishes unless you ask to keep it.

USask researchers and staff should first obtain credentials to be used by the script.
Install the [AWS Command Line Utilities](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) and then run:

```bash
aws configure sso
```

- Choose a meaningful SSO profile name
- The SSO Start URL will be the one you click to log into your AWS account
- The region should be ca-central-1
- When asked for the SSO registration scopes, press enter

After your SSO session is configured, run the following to verify you are logged in:

```bash
export AWS_PROFILE=NEW_PROFILE_NAME
aws sts get-caller-identity
```

## Requirements

- You must have Python 3.8 or higher already installed
- An S3 bucket in your AWS account
- AWS credentials that can access S3 and Amazon Transcribe

```bash
pip install -r requirements.txt
```
(Or use a virtual environment)

## Usage

```
usage: transcribe.py [-h] [-b BUCKET] [--prefix PREFIX] [--profile PROFILE]
                     [--region REGION] [-l LANGUAGE] [-s [N]] [-o PATH]
                     [--save-json PATH] [--keep-media] [--timeout TIMEOUT]
                     audio
```

### Arguments

| Argument | Default | Description |
| --- | --- | --- |
| `audio` | - | Path to the local audio/video file. Required. |
| `-b`, `--bucket BUCKET` | `$TRANSCRIBE_BUCKET` | S3 bucket for the upload and the transcript. Required, via the flag or the environment variable. |
| `--prefix PREFIX` | `transcribe` | Key prefix inside the bucket. |
| `--profile PROFILE` | `$AWS_PROFILE` | AWS profile to use. Falls back to the default credential chain when unset. |
| `--region REGION` | profile's region | Override the region the profile resolves to. |
| `-l`, `--language LANGUAGE` | `en-US` | BCP-47 language code, or `auto` to let Transcribe identify the language. |
| `-s`, `--speakers [N]` | off | Enable speaker diarization, expecting up to `N` speakers. Bare `-s` means 2. |
| `-o`, `--output PATH` | stdout | Write the transcript to this file instead of stdout. |
| `--save-json PATH` | - | Also save the raw Transcribe JSON here. |
| `--keep-media` | off | Leave the uploaded audio in S3 instead of deleting it. |
| `--timeout TIMEOUT` | `3600` | Seconds to wait for the job before giving up. The job keeps running in AWS. |
| `-h`, `--help` | - | Show the help text and exit. |

Recognized media extensions are `.amr`, `.flac`, `.m4a`, `.mp3`, `.mp4`,
`.ogg`, `.wav`, and `.webm`; for anything else the format is left for
Transcribe to work out.

## Examples

Set the bucket once and transcribe with the default English model:

```bash
export TRANSCRIBE_BUCKET=my-bucket
python transcribe.py interview.mp3
```

A two-speaker interview, saved to a file:

```bash
python transcribe.py interview.mp3 -s -o interview.txt
```

Up to six speakers, keeping the raw JSON for later analysis:

```bash
python transcribe.py standup.m4a --speakers 6 --save-json standup.json
```

Let Transcribe detect the language, using a named profile and region:

```bash
python transcribe.py recording.wav --language auto \
  --profile research --region ca-central-1
```

Redirect the transcript while still watching progress:

```bash
python transcribe.py lecture.mp4 > lecture.txt
```

With `--speakers`, the transcript is rendered one line per speaker turn:

```
spk_0: Good morning, everyone.
spk_1: Morning. Did the build finish?
```

Without it, you get the single continuous transcript Transcribe returns.

## Output and cleanup

- The transcript goes to stdout, or to `--output`.
- The Transcribe JSON stays in S3 at `s3://BUCKET/PREFIX/transcripts/JOB.json`,
  and its location is printed when the run finishes.
- The uploaded audio at `s3://BUCKET/PREFIX/media/JOB.EXT` is deleted on the way
  out, including after a failure, unless `--keep-media` is set.
- Job names are built from the filename plus a UTC timestamp and a short random
  suffix, so repeated runs on the same file do not collide.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Transcript produced. |
| `1` | The job failed, timed out, or an AWS call errored. |
| `2` | Bad invocation: the file does not exist, or no bucket was given. |
| `3` | Interrupted with Ctrl-C. The job may still be running in AWS. |

## License

[MIT](LICENSE)
