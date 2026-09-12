# 07 — Building a Recommendation System using MXNet Data on Amazon SageMaker

Neural matrix factorisation in MXNet (Gluon), trained on MovieLens-style interaction data with
SageMaker script mode, served two ways: precomputed top-N in DynamoDB for the common case, and a
real-time endpoint for scoring arbitrary user/item pairs.

```
S3 interactions ──> Training (MXNet script mode, ml.g4dn or ml.m5) ──> model.tar.gz
                                      │
                    ┌─────────────────┴──────────────────┐
            Batch Transform                        Real-time endpoint
            top-N per user                          score(user, item)
                    │                                     │
              DynamoDB recommendations  <── Lambda API ───┘   GET /users/{id}/recommendations
```

Precomputing is the default because a recommendation request is a key lookup, not a model call:
single-digit-millisecond reads, no endpoint to keep warm, and the model can be as expensive as you
like. The endpoint exists for cold users and for scoring items that were not in the batch.

## Why MXNet here

The dataset is user/item interactions and the model is embeddings + an MLP head — a shape Gluon
expresses in ~40 lines with an imperative training loop that is easy to read and modify.
`src/training/train.py` is a complete, runnable Gluon implementation, not a wrapper around a
built-in algorithm.

## Model

| Piece | Choice |
|-------|--------|
| Embeddings | 64-dim user and item factors, shared with a bias term each |
| Head | concat(user, item, user*item) → 128 → 64 → 1 |
| Loss | L2 on explicit ratings, or logistic on implicit feedback with negative sampling |
| Negatives | 4 sampled negatives per positive for implicit mode |
| Metric | RMSE (explicit) or HitRate@10 / NDCG@10 (implicit, leave-one-out) |

## Layout

```
infra/          CDK: data bucket, SageMaker role, DynamoDB recommendations table, Lambda + HTTP API
src/data/       prepare.py — build interaction files, user/item index maps, leave-one-out splits
src/training/   train.py — Gluon model, training loop, SageMaker entrypoint; also loads for inference
src/serving/    handler.py — API Lambda; DynamoDB first, endpoint fallback
                load_recommendations.py — writes batch transform output into DynamoDB
notebooks/      01-train-and-evaluate.ipynb
```

## Prerequisites

- AWS CDK v2, Python 3.11+
- MovieLens 25M (or 100k while developing): https://grouplens.org/datasets/movielens/

## Deploy and train

```bash
cd infra && pip install -r requirements.txt && cdk deploy RecommenderStack
export DATA_BUCKET=... ROLE_ARN=...

python src/data/prepare.py --ratings ml-25m/ratings.csv --out ./data --implicit
aws s3 sync ./data "s3://$DATA_BUCKET/processed/"

python src/training/launch.py --bucket "$DATA_BUCKET" --role-arn "$ROLE_ARN" \
  --instance-type ml.g4dn.xlarge --epochs 8
```

## Serve

```bash
# nightly: batch transform -> DynamoDB
python src/serving/load_recommendations.py --bucket "$DATA_BUCKET" --table recommender-dev-recs

curl "$API_URL/users/42/recommendations?limit=10"
```

## Cost

Training MovieLens-25M for 8 epochs on `ml.g4dn.xlarge` is about 20 minutes (~$0.25). DynamoDB
on-demand and Lambda are pennies. The real-time endpoint is optional — leave
`enable_endpoint=false` in `cdk.json` context and you pay nothing when idle.

## Teardown

```bash
cd infra && cdk destroy RecommenderStack
```
