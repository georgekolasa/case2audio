# case2audio

Turn layout-heavy PDFs into clean narration text, then optionally send that text to
Amazon Polly and download the MP3.

The workflow is intentionally split in two:

1. Docling handles columns, reading order, OCR, and document structure locally.
2. A small cleanup layer removes narration noise before any paid AWS request happens.

Source PDFs, extracted text, and audio are ignored by Git so licensed course material does
not accidentally land on GitHub.

## Fast setup

Python 3.12 is recommended. On macOS with Homebrew:

```bash
brew install python@3.12
./scripts/bootstrap.sh
source .venv/bin/activate
case2audio doctor
```

If `doctor` says the AWS token is invalid, refresh the login before synthesis:

```bash
aws sts get-caller-identity
aws sso login --profile YOUR-PROFILE  # Only for an AWS SSO profile.
```

The first real extraction downloads Docling's local models, so it is much slower than later
runs.

## Test extraction without spending anything

```bash
mkdir -p inputs
cp /path/to/your-case.pdf inputs/
case2audio extract inputs/your-case.pdf --debug-dir generated/your-case/debug
```

This writes `generated/your-case.txt`. Read that once before using Polly. If a publisher's
notice survives, inspect `generated/your-case/debug/docling.md` and add a narrow rule:

```bash
case2audio extract inputs/your-case.pdf \
  --drop-regex '^confidential course copy$'
```

Tables are skipped by default because raw rows usually sound awful. Use
`--table-mode linearize` when their content matters. That mode uses Docling's ordinary visual
reading order, so inspect the result when a page mixes tables and sidebars.

## Configure AWS once

The CLI uses the standard AWS credential chain. It never stores keys in this repo.

```bash
aws configure
aws s3 mb s3://YOUR-UNIQUE-CASE2AUDIO-BUCKET --region us-east-2
```

The bucket must be in the same region as Polly. A narrowly scoped IAM identity needs these
actions:

- `polly:StartSpeechSynthesisTask`
- `polly:GetSpeechSynthesisTask`
- `s3:PutObject` on the chosen bucket so Polly can write the result
- `s3:GetObject` on the chosen bucket so the CLI can download it

Use your normal AWS security controls for the bucket: block public access and enable default
encryption. Delete generated objects according to your retention needs.

## One-command PDF to MP3

```bash
case2audio make inputs/your-case.pdf \
  --bucket YOUR-UNIQUE-CASE2AUDIO-BUCKET \
  --region us-east-2
```

Results appear under `generated/your-case/`:

```text
generated/your-case/
├── narration.txt
├── debug/
│   ├── docling.md
│   ├── narration-order.md
│   └── docling.json
└── audio/
    └── part-001.mp3
```

`part-001.mp3` is the complete audiobook for text under 95,000 characters. Longer documents
are split at paragraph and sentence boundaries into ordered files. Keeping those parts
separate avoids an unreliable binary MP3 join; adding automatic ffmpeg concatenation is the
obvious next feature if you start hitting the limit often.

Sidebars detected from their heading and page geometry are moved to a final `Sidebars` section.
This keeps a box in the left column from interrupting an unfinished sentence in the main article.

Useful options:

```bash
# Born-digital PDF: skip OCR for a faster run.
case2audio make inputs/case.pdf --no-ocr --bucket YOUR-BUCKET

# Use another voice and AWS profile.
case2audio make inputs/case.pdf \
  --voice Matthew \
  --engine neural \
  --profile school \
  --bucket YOUR-BUCKET

# Re-synthesize previously reviewed text without extracting again.
case2audio speak generated/case.txt --bucket YOUR-BUCKET
```

Voice support varies by engine and region. If Polly rejects a voice/engine combination, list
valid choices with:

```bash
aws polly describe-voices --engine neural --region us-east-2
```

## Development

```bash
source .venv/bin/activate
pytest
ruff check .
```

CI runs the fast unit tests and lint checks without downloading Docling models or calling AWS.
The AWS test uses fakes, so pull requests cannot create paid synthesis tasks.

## Publishing this repo

```bash
git remote add origin git@github.com:YOUR-USER/case2audio.git
git push -u origin main
```

Do not force-add anything under `inputs/` or `generated/`. Keep the resulting audio within the
personal/course-use rights granted by the source document's license.

## Reference docs

- [Docling basic usage](https://docling-project.github.io/docling/usage/)
- [Amazon Polly long audio tasks](https://docs.aws.amazon.com/polly/latest/dg/longer-console.html)
