# 08 — Building an Image Classification System with Amazon SageMaker

Transfer-learned image classifier using SageMaker's built-in Image Classification algorithm
(ResNet), with an S3-triggered Lambda that classifies every uploaded image and stores the label
in DynamoDB.

```
S3 images/raw ──> build manifests ──> Training (built-in image-classification, ResNet-50)
                                              │
                                       Endpoint (or serverless)
                                              │
S3 images/incoming ──(ObjectCreated)──> Classifier Lambda ──> DynamoDB predictions
                                              │
                                     low-confidence ──> S3 review/ prefix
```

## Why the built-in algorithm

For "is this a cat or a dog" style problems on a few thousand images, the built-in algorithm with
`use_pretrained_model=1` beats a hand-written PyTorch loop on time-to-result: no container to
build, sensible augmentation defaults, and it accepts plain image files with an augmented manifest
so you can skip RecordIO entirely. When you need a custom head, custom loss, or a modern backbone,
swap `launch.py` for a PyTorch estimator — the rest of the system does not change.

## Layout

```
infra/            CDK: buckets, role, classifier Lambda + S3 trigger, predictions table, alarms
src/data/         build_manifests.py — folder-per-class -> train/validation augmented manifests
src/training/     launch.py — training job, hyperparameters, optional HPO
src/inference/    handler.py — S3 trigger -> endpoint -> DynamoDB, with a confidence gate
notebooks/        01-train-image-classifier.ipynb
```

## Data layout expected

```
images/raw/
  cats/  img001.jpg ...
  dogs/  img214.jpg ...
```

`build_manifests.py` walks that tree, builds the class index, makes a stratified split, and writes
augmented manifest files (one JSON object per line) that SageMaker reads directly from S3.

## Deploy

```bash
cd infra && pip install -r requirements.txt && cdk deploy ImageClassifierStack
export IMAGES_BUCKET=... ROLE_ARN=...

aws s3 sync ./images/raw "s3://$IMAGES_BUCKET/raw/"
python src/data/build_manifests.py --bucket "$IMAGES_BUCKET" --prefix raw --out-prefix manifests
python src/training/launch.py --bucket "$IMAGES_BUCKET" --role-arn "$ROLE_ARN" --epochs 15
```

Deploy the trained model to the endpoint name the stack expects:

```bash
python src/training/launch.py --deploy --job-name <training-job-name> \
  --bucket "$IMAGES_BUCKET" --role-arn "$ROLE_ARN" --endpoint-name image-classifier-dev
```

Then drop an image in `s3://$IMAGES_BUCKET/incoming/` and read the result:

```bash
aws dynamodb scan --table-name image-classifier-dev-predictions --max-items 5
```

## Tuning notes

| Hyperparameter | Default here | Why |
|---|---|---|
| `use_pretrained_model` | 1 | ImageNet weights; the difference between 60% and 95% on small datasets |
| `num_layers` | 50 | ResNet-50. Drop to 18 for <1,000 images to avoid overfitting |
| `learning_rate` | 0.001 | Fine-tuning, not training from scratch |
| `mini_batch_size` | 32 | Raise with GPU memory; scale the learning rate with it |
| `augmentation_type` | `crop_color_transform` | Cheap regularisation; the main defence against small datasets |
| `early_stopping` | 1 | Patience 5 on validation accuracy |

## Cost

`ml.p3.2xlarge` is ~$4.28/hour — fine for a 20-minute fine-tune, expensive if you forget it.
`ml.g4dn.xlarge` (~$0.90/hour) is usually the better trade. The endpoint is the recurring cost:
use a serverless endpoint (`--serverless`) if traffic is bursty.

## Teardown

```bash
cd infra && cdk destroy ImageClassifierStack
aws sagemaker delete-endpoint --endpoint-name image-classifier-dev
```
