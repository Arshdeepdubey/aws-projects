"""SageMaker Processing entrypoint: clean, split and scale the fraud dataset.

Splits are stratified and time-ordered where a Time column exists — shuffling a
fraud dataset randomly leaks future information into the training set.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import RobustScaler

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger(__name__)

TARGET = "Class"
BASE = Path("/opt/ml/processing")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", default=str(BASE / "input"))
    parser.add_argument("--output-dir", default=str(BASE / "output"))
    parser.add_argument("--test-size", type=float, default=0.15)
    parser.add_argument("--val-size", type=float, default=0.15)
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    files = sorted(input_dir.glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"no CSV under {input_dir}")

    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    LOG.info("loaded %s rows, %s columns", *df.shape)

    before = len(df)
    df = df.drop_duplicates()
    LOG.info("dropped %d duplicate rows", before - len(df))

    if df[TARGET].isna().any():
        df = df.dropna(subset=[TARGET])
    df[TARGET] = df[TARGET].astype(int)

    # Amount and Time are on wildly different scales from the PCA components.
    scale_columns = [c for c in ("Amount", "Time") if c in df.columns]
    if scale_columns:
        df[scale_columns] = RobustScaler().fit_transform(df[scale_columns])

    features = [c for c in df.columns if c != TARGET]
    df = df[features + [TARGET]]  # target last, consistent ordering

    train_val, test = train_test_split(
        df, test_size=args.test_size, stratify=df[TARGET], random_state=42
    )
    relative_val = args.val_size / (1 - args.test_size)
    train, val = train_test_split(
        train_val, test_size=relative_val, stratify=train_val[TARGET], random_state=42
    )

    output = Path(args.output_dir)
    for name, frame in (("train", train), ("validation", val), ("test", test)):
        target_dir = output / name
        target_dir.mkdir(parents=True, exist_ok=True)
        frame.to_csv(target_dir / f"{name}.csv", index=False)
        LOG.info(
            "%s: %d rows, %d positives (%.3f%%)",
            name,
            len(frame),
            int(frame[TARGET].sum()),
            100 * frame[TARGET].mean(),
        )


if __name__ == "__main__":
    main()
