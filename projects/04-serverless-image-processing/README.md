# 04 — Building a Serverless Image Processing System

Drop an image in the uploads bucket; a few seconds later you have thumbnails in the derived bucket
and a metadata record in DynamoDB. Built with AWS SAM because the whole thing is S3 → SQS → Lambda
glue that SAM's event shorthand expresses in a few lines.

```
S3 uploads ──(ObjectCreated)──> SQS queue ──> Lambda processor ──> S3 derived (thumb/small/medium)
                                   │  DLQ                │
                                   ▼                     └──> DynamoDB images table
                              failed messages
                                                      API Gateway ──> Lambda api ──> presigned PUT / GET record
```

SQS between S3 and Lambda is the part worth copying: it absorbs upload bursts, gives you a real
retry policy, and a dead-letter queue you can inspect instead of losing failures in Lambda's
async retry queue.

## Layout

```
template.yaml         SAM template — buckets, queue, table, functions, API
src/processor/        Image processing Lambda (Pillow): validate, resize, strip EXIF, store
src/api/              HTTP API: POST /uploads -> presigned URL, GET /images/{id} -> metadata
events/               Sample events for `sam local invoke`
tests/                pytest unit tests that run without AWS
```

## Prerequisites

- AWS SAM CLI, Docker (SAM builds Pillow in a Lambda-like container), Python 3.12
- `pip install -r requirements-dev.txt` for the tests

## Deploy

```bash
sam build --use-container
sam deploy --guided        # first time; writes samconfig.toml
sam deploy                 # subsequently

aws s3 cp photo.jpg "s3://$(aws cloudformation describe-stacks --stack-name image-processing \
  --query "Stacks[0].Outputs[?OutputKey=='UploadsBucket'].OutputValue" --output text)/incoming/photo.jpg"
```

Or `make deploy`.

## Test locally

```bash
sam build --use-container
sam local invoke ProcessorFunction -e events/sqs-s3-put.json
pytest -q
```

`sam local invoke` needs real S3 objects, so point `UPLOADS_BUCKET` at a deployed bucket or run
against `moto` — the unit tests take the second route and need no AWS at all.

## What the processor does

1. Rejects anything over `MAX_IMAGE_BYTES` or outside the allowed content types (a decompression
   bomb check runs before the full decode — `Image.open` is lazy, so the size check is cheap).
2. Corrects orientation from EXIF, then strips all EXIF from the output (GPS tags in user uploads
   are a privacy liability).
3. Writes `thumb` (150px), `small` (480px), `medium` (1024px) WebP + JPEG variants, longest side,
   aspect preserved, never upscaled.
4. Puts one DynamoDB item per image with dimensions, byte sizes, checksum and the derived keys.
5. Anything that throws goes back to SQS and, after `maxReceiveCount`, to the DLQ.

## Cost

Effectively free at low volume: Lambda and SQS free tiers, S3 storage pennies, DynamoDB on-demand.
1 million 3 MB images/month is roughly $15–25, dominated by Lambda duration and S3 PUTs.

## Teardown

```bash
sam delete
```

Buckets must be empty first: `aws s3 rm s3://<bucket> --recursive`.
