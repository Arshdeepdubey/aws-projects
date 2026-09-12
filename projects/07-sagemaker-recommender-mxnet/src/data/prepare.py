"""Turn a raw ratings file into training data plus index maps.

    python prepare.py --ratings ml-25m/ratings.csv --out ./data --implicit

Outputs:
    train/train.csv         userIdx,itemIdx,label
    validation/val.csv      leave-one-out holdout per user
    index/users.json        original id -> dense index
    index/items.json
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ratings", required=True, help="CSV with userId,movieId,rating,timestamp")
    parser.add_argument("--out", default="./data")
    parser.add_argument("--implicit", action="store_true",
                        help="Treat ratings >= --threshold as positives and sample negatives.")
    parser.add_argument("--threshold", type=float, default=4.0)
    parser.add_argument("--negatives", type=int, default=4)
    parser.add_argument("--min-interactions", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)
    df = pd.read_csv(args.ratings)
    df.columns = [c.lower() for c in df.columns]
    user_col = "userid"
    item_col = "movieid" if "movieid" in df.columns else "itemid"

    LOG.info("loaded %d interactions", len(df))

    # Drop cold users/items: an embedding trained on two examples is noise.
    counts = df[user_col].value_counts()
    df = df[df[user_col].isin(counts[counts >= args.min_interactions].index)]
    item_counts = df[item_col].value_counts()
    df = df[df[item_col].isin(item_counts[item_counts >= args.min_interactions].index)]
    LOG.info("after filtering: %d interactions, %d users, %d items",
             len(df), df[user_col].nunique(), df[item_col].nunique())

    users = {int(u): i for i, u in enumerate(sorted(df[user_col].unique()))}
    items = {int(m): i for i, m in enumerate(sorted(df[item_col].unique()))}

    df["userIdx"] = df[user_col].map(users)
    df["itemIdx"] = df[item_col].map(items)
    df = df.sort_values("timestamp") if "timestamp" in df.columns else df

    if args.implicit:
        positives = df[df["rating"] >= args.threshold][["userIdx", "itemIdx"]].copy()
        positives["label"] = 1.0
        LOG.info("implicit positives: %d", len(positives))

        # Uniform negative sampling. Popularity-weighted sampling usually scores better;
        # swap rng.integers for a popularity distribution if you want to try it.
        n_items = len(items)
        negatives = pd.DataFrame(
            {
                "userIdx": np.repeat(positives["userIdx"].to_numpy(), args.negatives),
                "itemIdx": rng.integers(0, n_items, size=len(positives) * args.negatives),
                "label": 0.0,
            }
        )
        data = pd.concat([positives, negatives], ignore_index=True)
    else:
        data = df[["userIdx", "itemIdx", "rating"]].rename(columns={"rating": "label"})

    # Leave-one-out: the most recent interaction per user is the holdout.
    data = data.sample(frac=1.0, random_state=args.seed).reset_index(drop=True)
    holdout = data[data["label"] > 0].groupby("userIdx", as_index=False).tail(1)
    train = data.drop(index=holdout.index)

    out = Path(args.out)
    for name, frame in (("train", train), ("validation", holdout)):
        directory = out / name
        directory.mkdir(parents=True, exist_ok=True)
        frame.to_csv(directory / f"{name}.csv", index=False)
        LOG.info("%s: %d rows", name, len(frame))

    index_dir = out / "index"
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "users.json").write_text(json.dumps(users))
    (index_dir / "items.json").write_text(json.dumps(items))

    (out / "metadata.json").write_text(
        json.dumps({"num_users": len(users), "num_items": len(items), "implicit": args.implicit}, indent=2)
    )
    LOG.info("wrote index maps: %d users, %d items", len(users), len(items))


if __name__ == "__main__":
    main()
