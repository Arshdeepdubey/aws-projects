"""MXNet/Gluon neural matrix factorisation, SageMaker script mode.

    python train.py --train ./data/train --validation ./data/validation --epochs 5

The model: user and item embeddings plus bias terms, concatenated with their
element-wise product and passed through a small MLP. That product term is what
lets the network express "this user likes this genre" without relearning the dot
product from scratch.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path

import mxnet as mx
import numpy as np
import pandas as pd
from mxnet import autograd, gluon, nd
from mxnet.gluon import nn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger(__name__)


class NeuralMF(nn.HybridBlock):
    def __init__(self, num_users: int, num_items: int, factors: int = 64, hidden=(128, 64), **kwargs):
        super().__init__(**kwargs)
        with self.name_scope():
            self.user_embedding = nn.Embedding(num_users, factors)
            self.item_embedding = nn.Embedding(num_items, factors)
            self.user_bias = nn.Embedding(num_users, 1)
            self.item_bias = nn.Embedding(num_items, 1)

            self.mlp = nn.HybridSequential()
            with self.mlp.name_scope():
                for units in hidden:
                    self.mlp.add(nn.Dense(units, activation="relu"))
                    self.mlp.add(nn.Dropout(0.2))
                self.mlp.add(nn.Dense(1))

    def hybrid_forward(self, F, users, items):  # noqa: N803
        u = self.user_embedding(users)
        i = self.item_embedding(items)
        features = F.concat(u, i, u * i, dim=1)
        score = self.mlp(features)
        return score + self.user_bias(users) + self.item_bias(items)


def load_split(path: str) -> pd.DataFrame:
    p = Path(path)
    files = sorted(p.glob("*.csv")) if p.is_dir() else [p]
    if not files:
        raise FileNotFoundError(f"no CSV under {path}")
    return pd.concat([pd.read_csv(f) for f in files], ignore_index=True)


def make_loader(df: pd.DataFrame, batch_size: int, shuffle: bool) -> gluon.data.DataLoader:
    dataset = gluon.data.ArrayDataset(
        nd.array(df["userIdx"].to_numpy(), dtype="int32"),
        nd.array(df["itemIdx"].to_numpy(), dtype="int32"),
        nd.array(df["label"].to_numpy(), dtype="float32"),
    )
    return gluon.data.DataLoader(dataset, batch_size=batch_size, shuffle=shuffle, last_batch="rollover")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", default=os.environ.get("SM_CHANNEL_TRAIN", "/opt/ml/input/data/train"))
    parser.add_argument("--validation", default=os.environ.get("SM_CHANNEL_VALIDATION", "/opt/ml/input/data/validation"))
    parser.add_argument("--model-dir", default=os.environ.get("SM_MODEL_DIR", "/opt/ml/model"))
    parser.add_argument("--num-users", type=int, default=0, help="0 = infer from the data")
    parser.add_argument("--num-items", type=int, default=0)
    parser.add_argument("--factors", type=int, default=64)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=4096)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--weight-decay", type=float, default=1e-5)
    parser.add_argument("--implicit", type=int, default=1)
    args = parser.parse_args()

    train_df = load_split(args.train)
    val_df = load_split(args.validation)

    num_users = args.num_users or int(max(train_df["userIdx"].max(), val_df["userIdx"].max())) + 1
    num_items = args.num_items or int(max(train_df["itemIdx"].max(), val_df["itemIdx"].max())) + 1
    LOG.info("train=%d val=%d users=%d items=%d", len(train_df), len(val_df), num_users, num_items)

    ctx = mx.gpu() if mx.context.num_gpus() else mx.cpu()
    LOG.info("context: %s", ctx)

    net = NeuralMF(num_users, num_items, factors=args.factors)
    net.initialize(mx.init.Xavier(), ctx=ctx)
    net.hybridize()

    loss_fn = (
        gluon.loss.SigmoidBinaryCrossEntropyLoss(from_sigmoid=False)
        if args.implicit
        else gluon.loss.L2Loss()
    )
    trainer = gluon.Trainer(
        net.collect_params(),
        "adam",
        {"learning_rate": args.learning_rate, "wd": args.weight_decay},
    )

    train_loader = make_loader(train_df, args.batch_size, shuffle=True)
    val_loader = make_loader(val_df, args.batch_size, shuffle=False)

    history = []
    for epoch in range(1, args.epochs + 1):
        started = time.time()
        total, seen = 0.0, 0

        for users, items, labels in train_loader:
            users = users.as_in_context(ctx)
            items = items.as_in_context(ctx)
            labels = labels.as_in_context(ctx)

            with autograd.record():
                predictions = net(users, items).reshape(-1)
                loss = loss_fn(predictions, labels)
            loss.backward()
            trainer.step(labels.shape[0])

            total += float(loss.sum().asscalar())
            seen += labels.shape[0]

        val_loss, val_metric = evaluate(net, val_loader, loss_fn, ctx, args.implicit)
        epoch_stats = {
            "epoch": epoch,
            "train_loss": total / max(seen, 1),
            "validation_loss": val_loss,
            ("auc" if args.implicit else "rmse"): val_metric,
            "seconds": round(time.time() - started, 1),
        }
        history.append(epoch_stats)
        LOG.info(json.dumps(epoch_stats))

    model_dir = Path(args.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    net.export(str(model_dir / "model"), epoch=0)  # model-symbol.json + model-0000.params
    (model_dir / "model_config.json").write_text(
        json.dumps(
            {
                "num_users": num_users,
                "num_items": num_items,
                "factors": args.factors,
                "implicit": bool(args.implicit),
                "history": history,
            },
            indent=2,
        )
    )
    LOG.info("saved model to %s", model_dir)


def evaluate(net, loader, loss_fn, ctx, implicit: int) -> tuple[float, float]:
    total, seen = 0.0, 0
    scores, targets = [], []

    for users, items, labels in loader:
        users = users.as_in_context(ctx)
        items = items.as_in_context(ctx)
        labels = labels.as_in_context(ctx)

        predictions = net(users, items).reshape(-1)
        total += float(loss_fn(predictions, labels).sum().asscalar())
        seen += labels.shape[0]

        scores.append(predictions.asnumpy())
        targets.append(labels.asnumpy())

    y_score = np.concatenate(scores) if scores else np.array([])
    y_true = np.concatenate(targets) if targets else np.array([])

    if implicit:
        metric = _auc(y_true, 1 / (1 + np.exp(-y_score)))
    else:
        metric = float(np.sqrt(np.mean((y_true - y_score) ** 2))) if len(y_true) else 0.0

    return total / max(seen, 1), metric


def _auc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """Rank-based AUC without pulling in scikit-learn on the training container."""
    if len(y_true) == 0 or len(set(y_true.tolist())) < 2:
        return 0.0
    order = np.argsort(y_score)
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, len(y_score) + 1)
    positives = y_true > 0
    n_pos, n_neg = positives.sum(), (~positives).sum()
    return float((ranks[positives].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


# ------------------------------------------------------------- SageMaker hooks
def model_fn(model_dir: str):
    """Called by the MXNet serving container to load the exported model."""
    ctx = mx.cpu()
    net = gluon.nn.SymbolBlock.imports(
        f"{model_dir}/model-symbol.json",
        ["data0", "data1"],
        f"{model_dir}/model-0000.params",
        ctx=ctx,
    )
    return net


def transform_fn(net, data, input_content_type, output_content_type):
    """Score {"pairs": [[userIdx, itemIdx], ...]} and return the scores."""
    payload = json.loads(data)
    pairs = payload["pairs"] if isinstance(payload, dict) else payload

    users = nd.array([p[0] for p in pairs], dtype="int32")
    items = nd.array([p[1] for p in pairs], dtype="int32")

    logits = net(users, items).reshape(-1).asnumpy()
    scores = (1 / (1 + np.exp(-logits))).tolist()

    return json.dumps({"scores": scores}), "application/json"


if __name__ == "__main__":
    main()
