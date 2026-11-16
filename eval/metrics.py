from __future__ import annotations

import random
from collections import Counter
from typing import Any, Callable


def exact(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol


def within(a: float, b: float, tolerance: float = 0.5) -> bool:
    return abs(a - b) <= tolerance + 1e-9


def mae(pairs: list[tuple[float, float]]) -> float:
    return sum(abs(a - b) for a, b in pairs) / max(1, len(pairs))


def bias(pairs: list[tuple[float, float]]) -> float:
    return sum(a - b for a, b in pairs) / max(1, len(pairs))


def qwk(y1: list[float], y2: list[float], minimum: float = 0.0, maximum: float = 10.0, step: float = 0.5) -> float:
    if len(y1) != len(y2) or not y1:
        return 0.0
    bins = int(round((maximum - minimum) / step)) + 1
    def to_bin(value: float) -> int:
        return max(0, min(bins - 1, int(round((value - minimum) / step))))
    a = [to_bin(v) for v in y1]
    b = [to_bin(v) for v in y2]
    observed = [[0.0] * bins for _ in range(bins)]
    for x, y in zip(a, b):
        observed[x][y] += 1
    hist_a = Counter(a)
    hist_b = Counter(b)
    n = len(a)
    expected = [[hist_a[i] * hist_b[j] / n for j in range(bins)] for i in range(bins)]
    num = 0.0
    den = 0.0
    for i in range(bins):
        for j in range(bins):
            weight = (i - j) ** 2
            num += observed[i][j] * weight
            den += expected[i][j] * weight
    if den == 0:
        return 1.0 if a == b else 0.0
    return 1.0 - num / den


def bootstrap_ci(values: list[float], statistic: Callable[[list[float]], float], n: int = 2000, seed: int = 7, alpha: float = 0.05) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    samples = []
    for _ in range(n):
        draw = [values[rng.randrange(len(values))] for _ in values]
        samples.append(statistic(draw))
    samples.sort()
    low = samples[int((alpha / 2) * n)]
    high = samples[int((1 - alpha / 2) * n) - 1]
    return low, high


def error_prf(predicted: list[dict], reference: list[dict]) -> dict[str, float]:
    def key(item: dict) -> tuple:
        return (int(item.get("sid", 0)), str(item.get("original", "")).strip().lower())
    pred = {key(item) for item in predicted}
    ref = {key(item) for item in reference}
    tp = len(pred & ref)
    precision = tp / max(1, len(pred))
    recall = tp / max(1, len(ref))
    f1 = 2 * precision * recall / max(1e-9, precision + recall)
    return {"precision": precision, "recall": recall, "f1": f1, "tp": tp, "predicted": len(pred), "reference": len(ref)}


def evaluate(records: list[dict[str, Any]]) -> dict[str, Any]:
    pairs = []
    for record in records:
        reference = float(record["reference_score"])
        predicted = float(record["predicted"])
        pairs.append((predicted, reference))
    if not pairs:
        raise ValueError("no records")
    exact_rate = sum(1 for a, b in pairs if exact(a, b)) / len(pairs)
    within_rate = sum(1 for a, b in pairs if within(a, b)) / len(pairs)
    y_pred = [a for a, _ in pairs]
    y_ref = [b for _, b in pairs]
    q = qwk(y_pred, y_ref)
    all_errors_pred = []
    all_errors_ref = []
    for record in records:
        all_errors_pred.extend(record.get("predicted_errors", []))
        all_errors_ref.extend(record.get("errors", []))
    error_metrics = error_prf(all_errors_pred, all_errors_ref) if all_errors_ref else {"precision": None, "recall": None}
    stability = []
    for record in records:
        runs = record.get("runs") or []
        if runs:
            stability.append(max(runs) - min(runs))
    return {
        "n": len(pairs),
        "exact_rate": exact_rate,
        "within_0_5": within_rate,
        "mae": mae(pairs),
        "bias": bias(pairs),
        "qwk": q,
        "error_precision": error_metrics["precision"],
        "error_recall": error_metrics["recall"],
        "stability_exact": (sum(1 for r in stability if r == 0) / len(stability)) if stability else None,
        "stability_max_range": max(stability) if stability else None,
        "synthetic": any(bool(r.get("synthetic")) for r in records),
        "guard_violations": sum(int(r.get("guard_violations", 0)) for r in records),
        "ci_within": bootstrap_ci([1.0 if within(a, b) else 0.0 for a, b in pairs], lambda xs: sum(xs) / len(xs)),
        "ci_mae": bootstrap_ci([abs(a - b) for a, b in pairs], lambda xs: sum(xs) / len(xs)),
    }
