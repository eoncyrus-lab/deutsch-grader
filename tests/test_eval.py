from eval.gate import gate
from eval.metrics import evaluate, mae, qwk, within


def test_metrics_basics():
    assert within(9.0, 9.5)
    assert not within(9.0, 9.6)
    assert mae([(9.5, 9.5), (9.0, 9.5)]) == 0.25
    assert qwk([1, 2, 3], [1, 2, 3]) == 1.0
    assert qwk([1, 2, 3], [1, 2, 3], minimum=1, maximum=3, step=1) == 1.0


def test_evaluate_and_gate_refuses_synthetic_release():
    records = [
        {"id": "a", "synthetic": True, "reference_score": 9.5, "predicted": 9.5, "runs": [9.5, 9.5, 9.5]},
        {"id": "b", "synthetic": True, "reference_score": 4.0, "predicted": 4.0, "runs": [4.0, 4.0, 4.0]},
    ]
    report = evaluate(records)
    assert report["n"] == 2
    assert report["synthetic"] is True
    failures = gate(report, None, release=True)
    assert any("synthetic" in failure for failure in failures)


def test_gate_accepts_large_clean_report():
    report = {
        "synthetic": False, "n": 50, "qwk": 0.91, "within_0_5": 0.96,
        "mae": 0.12, "stability_exact": 0.95, "guard_violations": 0,
    }
    assert gate(report, None, release=True) == []
