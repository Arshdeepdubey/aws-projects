"""Train (and optionally deploy) the SageMaker built-in image classifier.

    python launch.py --bucket my-bucket --role-arn arn:... --epochs 15
    python launch.py --deploy --job-name image-classification-2026-09-11-... \
        --bucket my-bucket --role-arn arn:... --endpoint-name image-classifier-dev
"""

from __future__ import annotations

import argparse
import json
import logging

import boto3
import sagemaker
from sagemaker import image_uris
from sagemaker.estimator import Estimator
from sagemaker.inputs import TrainingInput
from sagemaker.serverless import ServerlessInferenceConfig

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger(__name__)


def class_counts(bucket: str, manifest_prefix: str) -> tuple[int, int]:
    s3 = boto3.client("s3")
    index = json.loads(
        s3.get_object(Bucket=bucket, Key=f"{manifest_prefix}/class_index.json")["Body"].read()
    )
    manifest = s3.get_object(Bucket=bucket, Key=f"{manifest_prefix}/train.manifest")["Body"].read()
    num_samples = sum(1 for line in manifest.decode().splitlines() if line.strip())
    return len(index["classes"]), num_samples


def train(args: argparse.Namespace) -> str:
    session = sagemaker.Session()
    num_classes, num_samples = class_counts(args.bucket, args.manifest_prefix)
    LOG.info("num_classes=%d num_training_samples=%d", num_classes, num_samples)

    container = image_uris.retrieve("image-classification", session.boto_region_name, version="1")

    estimator = Estimator(
        image_uri=container,
        role=args.role_arn,
        instance_count=1,
        instance_type=args.instance_type,
        volume_size=50,
        max_run=int(args.max_hours * 3600),
        output_path=f"s3://{args.bucket}/models/",
        base_job_name="image-classification",
        sagemaker_session=session,
    )

    estimator.set_hyperparameters(
        num_classes=num_classes,
        num_training_samples=num_samples,
        # ImageNet weights: on a few thousand images this is the whole ballgame.
        use_pretrained_model=1,
        num_layers=args.num_layers,
        image_shape="3,224,224",
        mini_batch_size=args.batch_size,
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        optimizer="adam",
        augmentation_type="crop_color_transform",
        early_stopping=1,
        early_stopping_patience=5,
        early_stopping_min_epochs=5,
        precision_dtype="float32",
        top_k=min(3, num_classes),
    )

    attributes = ["source-ref", "class"]
    inputs = {
        "train": TrainingInput(
            s3_data=f"s3://{args.bucket}/{args.manifest_prefix}/train.manifest",
            s3_data_type="AugmentedManifestFile",
            attribute_names=attributes,
            content_type="application/x-image",
            record_wrapping="RecordIO",
            input_mode="Pipe",
        ),
        "validation": TrainingInput(
            s3_data=f"s3://{args.bucket}/{args.manifest_prefix}/validation.manifest",
            s3_data_type="AugmentedManifestFile",
            attribute_names=attributes,
            content_type="application/x-image",
            record_wrapping="RecordIO",
            input_mode="Pipe",
        ),
    }

    estimator.fit(inputs, wait=args.wait)
    job_name = estimator.latest_training_job.name
    LOG.info("training job: %s", job_name)
    return job_name


def deploy(args: argparse.Namespace) -> None:
    session = sagemaker.Session()
    estimator = Estimator.attach(args.job_name, sagemaker_session=session)

    if args.serverless:
        estimator.deploy(
            endpoint_name=args.endpoint_name,
            serverless_inference_config=ServerlessInferenceConfig(
                memory_size_in_mb=4096, max_concurrency=5
            ),
        )
    else:
        estimator.deploy(
            initial_instance_count=1,
            instance_type=args.endpoint_instance_type,
            endpoint_name=args.endpoint_name,
        )

    LOG.info("deployed to endpoint %s", args.endpoint_name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--role-arn", required=True)
    parser.add_argument("--manifest-prefix", default="manifests")
    parser.add_argument("--instance-type", default="ml.g4dn.xlarge")
    parser.add_argument("--num-layers", type=int, default=50)
    parser.add_argument("--epochs", type=int, default=15)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--max-hours", type=float, default=2.0)
    parser.add_argument("--wait", action="store_true")

    parser.add_argument("--deploy", action="store_true", help="Deploy an existing job instead of training.")
    parser.add_argument("--job-name")
    parser.add_argument("--endpoint-name", default="image-classifier-dev")
    parser.add_argument("--endpoint-instance-type", default="ml.m5.large")
    parser.add_argument("--serverless", action="store_true")

    args = parser.parse_args()

    if args.deploy:
        if not args.job_name:
            parser.error("--deploy requires --job-name")
        deploy(args)
    else:
        train(args)


if __name__ == "__main__":
    main()
