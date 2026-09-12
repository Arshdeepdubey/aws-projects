"""SageMaker script-mode training entrypoint for fraud detection (XGBoost).

Run locally:
    python train.py --train ./data/train.csv --validation ./data/val.csv --model-dir ./model

On SageMaker the channels arrive as directories in SM_CHANNEL_* and the model must
be written to SM_MODEL_DIR.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    roc_auc_score,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger(__name__)

TARGET = "Class"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", default=os.environ.get("SM_CHANNEL_TRAIN", "/opt/ml/input/data/train"))
    parser.add_argument("--validation", default=os.environ.get("SM_CHANNEL_VALIDATION", "/opt/ml/input/data/validation"))
    parser.add_argument("--model-dir", default=os.environ.get("SM_MODEL_DIR", "/opt/ml/model"))
    parser.add_argument("--output-data-dir", default=os.environ.get("SM_OUTPUT_DATA_DIR", "/opt/ml/output/data"))

    parser.add_argument("--num-round", type=int, default=400)
    parser.add_argument("--max-depth", type=int, default=6)
    parser.add_argument("--eta", type=float, default=0.1)
    parser.add_argument("--subsample", type=float, default=0.8)
    parser.add_argument("--colsample-bytree", type=float, default=0.8)
    parser.add_argument("--min-child-weight", type=float, default=1.0)
    parser.add_argument("--early-stopping-rounds", type=int, default=30)
    parser.add_argument("--target-recall", type=float, default=0.85,
                        help="If > 0, pick the threshold that reaches this recall; otherwise maximise F1.")

    return parser.parse_args()


def load(path: str) -> pd.DataFrame:
    p = Path(path)
    files = sorted(p.glob("*.csv")) if p.is_dir() else [p]
    if not files:
        raise FileNotFoundError(f"no CSV files under {path}")
    frames = [pd.read_csv(f) for f in files]
    return pd.concat(frames, ignore_index=True)


def split_xy(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    if TARGET not in df.columns:
        raise ValueError(f"expected a '{TARGET}' column, got {list(df.columns)[:10]}")
    return df.drop(columns=[TARGET]), df[TARGET].astype(int)


def choose_threshold(y_true: np.ndarray, scores: np.ndarray, target_recall: float) -> float:
    """0.5 is almost never the right cut for imbalanced problems."""
    precision, recall, thresholds = precision_recall_curve(y_true, scores)

    if target_recall > 0:
        # Highest precision among thresholds that still hit the recall target.
        viable = [
            (p, t) for p, r, t in zip(precision[:-1], recall[:-1], thresholds) if r >= target_recall
        ]
        if viable:
            return float(max(viable, key=lambda pair: pair[0])[1])
        LOG.warning("target recall %.2f unreachable; falling back to best F1", target_recall)

    f1_scores = 2 * precision[:-1] * recall[:-1] / np.clip(precision[:-1] + recall[:-1], 1e-9, None)
    return float(thresholds[int(np.argmax(f1_scores))])


def main() -> None:
    args = parse_args()

    train_df = load(args.train)
    val_df = load(args.validation)
    LOG.info("train=%s validation=%s", train_df.shape, val_df.shape)

    x_train, y_train = split_xy(train_df)
    x_val, y_val = split_xy(val_df)

    positives = int(y_train.sum())
    negatives = int(len(y_train) - positives)
    scale_pos_weight = negatives / max(positives, 1)
    LOG.info("positives=%d negatives=%d scale_pos_weight=%.1f", positives, negatives, scale_pos_weight)

    dtrain = xgb.DMatrix(x_train, label=y_train)
    dval = xgb.DMatrix(x_val, label=y_val)

    params = {
        "objective": "binary:logistic",
        # AUC-PR, not accuracy or plain AUC: with 0.17% positives the ROC curve flatters everything.
        "eval_metric": ["aucpr", "auc"],
        "max_depth": args.max_depth,
        "eta": args.eta,
        "subsample": args.subsample,
        "colsample_bytree": args.colsample_bytree,
        "min_child_weight": args.min_child_weight,
        "scale_pos_weight": scale_pos_weight,
        "tree_method": "hist",
        "seed": 42,
    }

    evals_result: dict = {}
    booster = xgb.train(
        params,
        dtrain,
        num_boost_round=args.num_round,
        evals=[(dtrain, "train"), (dval, "validation")],
        early_stopping_rounds=args.early_stopping_rounds,
        evals_result=evals_result,
        verbose_eval=25,
    )

    scores = booster.predict(dval, iteration_range=(0, booster.best_iteration + 1))
    threshold = choose_threshold(y_val.to_numpy(), scores, args.target_recall)
    predictions = (scores >= threshold).astype(int)

    tn, fp, fn, tp = confusion_matrix(y_val, predictions, labels=[0, 1]).ravel()
    metrics = {
        "auc_pr": float(average_precision_score(y_val, scores)),
        "roc_auc": float(roc_auc_score(y_val, scores)),
        "f1": float(f1_score(y_val, predictions)),
        "precision": float(tp / (tp + fp)) if (tp + fp) else 0.0,
        "recall": float(tp / (tp + fn)) if (tp + fn) else 0.0,
        "threshold": threshold,
        "true_positives": int(tp),
        "false_positives": int(fp),
        "false_negatives": int(fn),
        "true_negatives": int(tn),
        "best_iteration": int(booster.best_iteration),
    }
    LOG.info("validation metrics: %s", json.dumps(metrics, indent=2))

    model_dir = Path(args.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(model_dir / "xgboost-model.json"))

    # Shipped inside model.tar.gz so inference can apply the same threshold and
    # validate the feature order it was trained on.
    (model_dir / "model_metadata.json").write_text(
        json.dumps(
            {
                "threshold": threshold,
                "features": list(x_train.columns),
                "metrics": metrics,
                "params": params,
            },
            indent=2,
        )
    )

    output_dir = Path(args.output_data_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "evaluation.json").write_text(
        json.dumps({"binary_classification_metrics": metrics}, indent=2)
    )

    LOG.info("saved model to %s", model_dir)


if __name__ == "__main__":
    main()
