from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def gate(report: dict, baseline: dict | None, *, release: bool, min_n: int = 50) -> list[str]:
    failures = []
    if report.get("synthetic") and release:
        failures.append("synthetic dataset cannot approve a release")
    if report.get("n", 0) < min_n and release:
        failures.append(f"release gate needs at least {min_n} labeled essays, got {report.get('n', 0)}")
    if report.get("guard_violations", 0) > 0:
        failures.append(f"minimal-edit violations: {report['guard_violations']}")
    if report.get("qwk") is not None and report["qwk"] < 0.80:
        failures.append(f"QWK below 0.80: {report['qwk']:.3f}")
    if report.get("within_0_5") is not None and report["within_0_5"] < 0.90:
        failures.append(f"within 0.5 below 90%: {report['within_0_5']:.3f}")
    if report.get("mae") is not None and report["mae"] > 0.35:
        failures.append(f"MAE above 0.35: {report['mae']:.3f}")
    if report.get("stability_exact") is not None and report["stability_exact"] < 0.90:
        failures.append(f"stability exact below 90%: {report['stability_exact']:.3f}")
    if baseline:
        if report.get("within_0_5", 0) < baseline.get("within_0_5", 0) - 0.03:
            failures.append("within 0.5 dropped by more than 3 points")
        if report.get("mae", 0) > baseline.get("mae", 0) + 0.05:
            failures.append("MAE increased by more than 0.05")
        if report.get("qwk", 1) < baseline.get("qwk", 1) - 0.03:
            failures.append("QWK dropped by more than 0.03")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report")
    parser.add_argument("--baseline")
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--min-n", type=int, default=50)
    args = parser.parse_args()
    report = load(Path(args.report))
    baseline = load(Path(args.baseline)) if args.baseline else None
    failures = gate(report, baseline, release=args.release, min_n=args.min_n)
    if failures:
        print("FAIL")
        for failure in failures:
            print("- " + failure)
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()
