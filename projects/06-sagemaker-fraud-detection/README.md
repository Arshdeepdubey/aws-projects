# 06 — Deploying a Complete Machine Learning Fraud Detection Solution using Amazon SageMaker

End-to-end credit-card fraud detection: data prep, XGBoost training with class imbalance handled
properly, model registry, a real-time endpoint behind a Lambda + API Gateway, and Model Monitor
for data drift.

```
S3 raw ──> Processing (sklearn) ──> S3 train/val/test
                                        │
                                   Training (XGBoost, script mode)
                                        │
                                  Model Registry (approval gate)
                                        │
                        Endpoint (autoscaled) <── Lambda <── API Gateway  POST /predict
                                        │
                                 Model Monitor ──> CloudWatch alarms
```

## The part that matters: imbalance

Fraud is ~0.17% of rows in the reference dataset. Accuracy is meaningless here — a model that
always predicts "legitimate" scores 99.83%. This template optimises **AUC-PR**, sets
`scale_pos_weight` to the negative/positive ratio, and picks the decision threshold on the
validation set by maximising F1 (or by fixing recall and reporting the precision you get). The
threshold is stored with the model and applied in the inference handler, not hardcoded at 0.5.

## Layout

```
infra/            CDK (Python): buckets, roles, model package group, Lambda + HTTP API, alarms
src/training/     train.py — SageMaker script mode entrypoint (XGBoost)
src/pipeline/     preprocess.py, run_pipeline.py — SageMaker Pipelines definition
src/inference/    handler.py — Lambda that calls the endpoint and applies the threshold
notebooks/        01-explore-and-train.ipynb — interactive path through the same code
```

## Prerequisites

- AWS CDK v2 (`npm i -g aws-cdk`), Python 3.11+, Docker not required
- `pip install -r requirements.txt`
- The Kaggle *Credit Card Fraud Detection* dataset (`creditcard.csv`), or any CSV with a binary
  `Class` column — put it at `s3://<data-bucket>/raw/creditcard.csv`

## Deploy the infrastructure

```bash
cd infra
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cdk bootstrap                # once per account/region
cdk deploy FraudDetectionStack
```

Outputs give you the data bucket, the SageMaker execution role ARN, and the API endpoint.

## Train

```bash
export DATA_BUCKET=$(aws cloudformation describe-stacks --stack-name FraudDetectionStack \
  --query "Stacks[0].Outputs[?OutputKey=='DataBucket'].OutputValue" --output text)

aws s3 cp creditcard.csv "s3://$DATA_BUCKET/raw/creditcard.csv"
python src/pipeline/run_pipeline.py --bucket "$DATA_BUCKET" --role-arn "$SAGEMAKER_ROLE_ARN"
```

The pipeline runs preprocessing, training, evaluation, and registers the model in the package group
with `PendingManualApproval`. Approve it to let the deploy step create/update the endpoint:

```bash
aws sagemaker update-model-package --model-package-arn <arn> \
  --model-approval-status Approved
```

## Predict

```bash
curl -s -X POST "$API_URL/predict" -H 'content-type: application/json' \
  -d '{"features": [0.0, -1.35, -0.07, 2.53, 1.37, -0.33, 0.46, 0.23, 0.09, 0.36, 149.62]}'
# {"fraudProbability":0.0021,"isFraud":false,"threshold":0.42,"modelVersion":"3"}
```

## Cost

The endpoint is the expensive part: `ml.m5.large` real-time is ~$0.115/hour (~$83/month) and runs
until you delete it. Training on `ml.m5.xlarge` for this dataset is minutes and cents. Set
`enable_endpoint=False` in `cdk.json` context and use batch transform if you only need offline
scoring, or use a serverless endpoint (configured in `infra/stacks/endpoint.py`) which bills per
request.

## Teardown

```bash
cd infra && cdk destroy FraudDetectionStack
aws sagemaker delete-endpoint --endpoint-name fraud-detection-dev   # if created outside CDK
```
