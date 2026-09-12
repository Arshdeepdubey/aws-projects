"""Start a SageMaker training job for the recommender.

    python launch.py --bucket my-bucket --role-arn arn:aws:iam::...:role/... --epochs 8
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from sagemaker.mxnet import MXNet

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--role-arn", required=True)
    parser.add_argument("--prefix", default="processed")
    parser.add_argument("--instance-type", default="ml.m5.xlarge")
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--factors", type=int, default=64)
    parser.add_argument("--batch-size", type=int, default=4096)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args()

    estimator = MXNet(
        entry_point="train.py",
        source_dir=str(HERE),
        role=args.role_arn,
        framework_version="1.9.0",
        py_version="py38",
        instance_type=args.instance_type,
        instance_count=1,
        output_path=f"s3://{args.bucket}/models/",
        base_job_name="recommender-mxnet",
        hyperparameters={
            "epochs": args.epochs,
            "factors": args.factors,
            "batch-size": args.batch_size,
            "learning-rate": args.learning_rate,
            "implicit": 1,
        },
        metric_definitions=[
            {"Name": "validation:auc", "Regex": r'"auc": ([0-9\.]+)'},
            {"Name": "train:loss", "Regex": r'"train_loss": ([0-9\.]+)'},
        ],
    )

    estimator.fit(
        {
            "train": f"s3://{args.bucket}/{args.prefix}/train/",
            "validation": f"s3://{args.bucket}/{args.prefix}/validation/",
        },
        wait=args.wait,
    )

    LOG.info("training job: %s", estimator.latest_training_job.name)
    LOG.info("model artifacts will be at s3://%s/models/", args.bucket)


if __name__ == "__main__":
    main()
