"""Define and start the SageMaker Pipeline: preprocess -> train -> evaluate -> register.

    python run_pipeline.py --bucket my-data-bucket --role-arn arn:aws:iam::...:role/...
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import sagemaker
from sagemaker.inputs import TrainingInput
from sagemaker.processing import ProcessingInput, ProcessingOutput
from sagemaker.sklearn.processing import SKLearnProcessor
from sagemaker.workflow.parameters import ParameterFloat, ParameterInteger, ParameterString
from sagemaker.workflow.pipeline import Pipeline
from sagemaker.workflow.pipeline_context import PipelineSession
from sagemaker.workflow.step_collections import RegisterModel
from sagemaker.workflow.steps import ProcessingStep, TrainingStep
from sagemaker.xgboost.estimator import XGBoost

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent


def build_pipeline(bucket: str, role_arn: str, project: str, model_package_group: str) -> Pipeline:
    session = PipelineSession(default_bucket=bucket)

    raw_uri = ParameterString("RawDataUri", default_value=f"s3://{bucket}/raw/")
    instance_type = ParameterString("TrainingInstanceType", default_value="ml.m5.xlarge")
    num_round = ParameterInteger("NumRound", default_value=400)
    target_recall = ParameterFloat("TargetRecall", default_value=0.85)
    approval_status = ParameterString("ModelApprovalStatus", default_value="PendingManualApproval")

    # ------------------------------------------------------------- preprocess
    processor = SKLearnProcessor(
        framework_version="1.2-1",
        role=role_arn,
        instance_type="ml.m5.xlarge",
        instance_count=1,
        base_job_name=f"{project}-preprocess",
        sagemaker_session=session,
    )

    processing_step = ProcessingStep(
        name="PreprocessFraudData",
        step_args=processor.run(
            code=str(HERE / "preprocess.py"),
            inputs=[ProcessingInput(source=raw_uri, destination="/opt/ml/processing/input")],
            outputs=[
                ProcessingOutput(output_name="train", source="/opt/ml/processing/output/train"),
                ProcessingOutput(output_name="validation", source="/opt/ml/processing/output/validation"),
                ProcessingOutput(output_name="test", source="/opt/ml/processing/output/test"),
            ],
        ),
    )

    # ---------------------------------------------------------------- train
    estimator = XGBoost(
        entry_point="train.py",
        source_dir=str(HERE.parent / "training"),
        framework_version="1.7-1",
        py_version="py3",
        role=role_arn,
        instance_type=instance_type,
        instance_count=1,
        output_path=f"s3://{bucket}/models/",
        base_job_name=f"{project}-train",
        sagemaker_session=session,
        hyperparameters={
            "num-round": num_round,
            "max-depth": 6,
            "eta": 0.1,
            "target-recall": target_recall,
        },
        metric_definitions=[
            {"Name": "validation:aucpr", "Regex": r"validation-aucpr:(\S+)"},
            {"Name": "validation:auc", "Regex": r"validation-auc:(\S+)"},
        ],
    )

    training_step = TrainingStep(
        name="TrainFraudModel",
        step_args=estimator.fit(
            {
                "train": TrainingInput(
                    s3_data=processing_step.properties.ProcessingOutputConfig.Outputs["train"].S3Output.S3Uri,
                    content_type="text/csv",
                ),
                "validation": TrainingInput(
                    s3_data=processing_step.properties.ProcessingOutputConfig.Outputs["validation"].S3Output.S3Uri,
                    content_type="text/csv",
                ),
            }
        ),
    )

    # -------------------------------------------------------------- register
    register_step = RegisterModel(
        name="RegisterFraudModel",
        estimator=estimator,
        model_data=training_step.properties.ModelArtifacts.S3ModelArtifacts,
        content_types=["application/json", "text/csv"],
        response_types=["application/json"],
        inference_instances=["ml.m5.large", "ml.m5.xlarge"],
        transform_instances=["ml.m5.xlarge"],
        model_package_group_name=model_package_group,
        approval_status=approval_status,
        description="XGBoost fraud detector with tuned decision threshold",
    )

    return Pipeline(
        name=f"{project}-pipeline",
        parameters=[raw_uri, instance_type, num_round, target_recall, approval_status],
        steps=[processing_step, training_step, register_step],
        sagemaker_session=session,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--role-arn", required=True)
    parser.add_argument("--project", default="fraud-detection")
    parser.add_argument("--model-package-group", default="fraud-detection-dev-models")
    parser.add_argument("--start", action="store_true", default=True)
    args = parser.parse_args()

    pipeline = build_pipeline(args.bucket, args.role_arn, args.project, args.model_package_group)
    pipeline.upsert(role_arn=args.role_arn)
    LOG.info("pipeline upserted: %s", pipeline.name)

    if args.start:
        execution = pipeline.start()
        LOG.info("started execution: %s", execution.arn)
        LOG.info("watch it with: aws sagemaker describe-pipeline-execution --pipeline-execution-arn %s", execution.arn)


if __name__ == "__main__":
    main()
