"""AC@k / MRR / category-accuracy correctness (no cluster, no LLM)."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from rca_bench.metrics import (  # noqa: E402
    IncidentEval, accuracy_at_k, mrr, category_accuracy, evaluate, format_report, rank_of,
)


def _evals():
    return [
        IncidentEval("1", "cpu-stress", "A", ["A", "B", "C"], "cpu-stress"),   # rank 1, cat ok
        IncidentEval("2", "cpu-stress", "B", ["A", "B", "C"], "memory-stress"),# rank 2, cat wrong
        IncidentEval("3", "pod-fail",   "C", ["A", "B", "D"], "pod-fail"),     # rank None, cat ok
    ]


def test_rank_of():
    assert rank_of("B", ["A", "B", "C"]) == 2
    assert rank_of("Z", ["A", "B", "C"]) is None


def test_accuracy_at_k():
    e = _evals()
    assert abs(accuracy_at_k(e, 1) - 1 / 3) < 1e-9     # only inc1
    assert abs(accuracy_at_k(e, 2) - 2 / 3) < 1e-9     # inc1, inc2
    assert abs(accuracy_at_k(e, 3) - 2 / 3) < 1e-9     # inc3 truth absent -> no
    assert accuracy_at_k([], 1) == 0.0


def test_mrr():
    e = _evals()
    # (1/1 + 1/2 + 0) / 3
    assert abs(mrr(e) - (1.0 + 0.5 + 0.0) / 3) < 1e-9


def test_category_accuracy():
    e = _evals()
    # inc1 ok, inc2 wrong, inc3 ok -> 2/3
    assert abs(category_accuracy(e) - 2 / 3) < 1e-9
    # no predictions -> None
    none = [IncidentEval("x", "cpu-stress", "A", ["A"], None)]
    assert category_accuracy(none) is None


def test_evaluate_overall_and_per_category():
    m = evaluate(_evals(), ks=(1, 3))
    assert m.count == 3
    assert abs(m.ac_at_k[1] - 1 / 3) < 1e-9
    assert "cpu-stress" in m.by_category and "pod-fail" in m.by_category
    # cpu-stress group has 2 incidents; AC@1 = 1/2 (inc1 only)
    assert abs(m.by_category["cpu-stress"].ac_at_k[1] - 0.5) < 1e-9
    # serializable + report renders
    assert isinstance(m.to_dict(), dict)
    assert "OVERALL" in format_report(m, ks=(1, 3))


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} RCA-metric tests passed.")


if __name__ == "__main__":
    _run_all()
