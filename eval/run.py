from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from eval.metrics import evaluate


def read_jsonl(path: Path) -> list[dict]:
    records = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: {exc}") from exc
    return records


def dataset_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def md(report: dict) -> str:
    def pct(value):
        return "—" if value is None else f"{value * 100:.1f}%"
    def num(value):
        return "—" if value is None else f"{value:.3f}"
    lines = [
        "# 评测报告", "",
        f"- 样本数：{report['n']}",
        f"- 数据集哈希：{report['dataset_hash'][:16]}",
        f"- 合成数据：{'是（仅用于流程验证）' if report['synthetic'] else '否'}",
        f"- 完全一致率：{pct(report['exact_rate'])}",
        f"- ±0.5 一致率：{pct(report['within_0_5'])}（95% CI {pct(report['ci_within'][0])}–{pct(report['ci_within'][1])}）",
        f"- MAE：{num(report['mae'])}（95% CI {num(report['ci_mae'][0])}–{num(report['ci_mae'][1])}）",
        f"- 偏差：{num(report['bias'])}",
        f"- QWK：{num(report['qwk'])}",
        f"- 错误精确率：{pct(report['error_precision'])}",
        f"- 错误召回率：{pct(report['error_recall'])}",
        f"- 稳定性完全一致：{pct(report['stability_exact'])}，最大极差 {num(report['stability_max_range'])}",
        f"- 最小改动违规：{report['guard_violations']}",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", required=True)
    parser.add_argument("--out", default="reports/eval.json")
    args = parser.parse_args()
    path = Path(args.set)
    records = read_jsonl(path)
    missing = [r.get("id") for r in records if "predicted" not in r or "reference_score" not in r]
    if missing:
        raise SystemExit(f"records missing predicted/reference_score: {missing[:5]}")
    report = evaluate(records)
    report.update({"dataset": str(path), "dataset_hash": dataset_hash(path)})
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(md(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
