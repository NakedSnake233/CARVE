import pytest

from scripts.audit_paper_table import metric_errors
from scripts.audit_review_evidence import summarize


def test_catches_submitted_mabc_inversions():
    for values in [(0.301, 0.407, 0.389, 0.386), (0.288, 0.391, 0.371, 0.371),
                   (0.308, 0.414, 0.397, 0.393)]:
        assert metric_errors(*values)
    assert not metric_errors(0.903, 0.976, 0.983, 0.943)
    assert metric_errors(0.1, 0.2, 0.3, 0.9)  # monotone AC alone is insufficient


def test_separates_service_and_category_conditioning_and_missing_outcomes():
    rows = [
        dict(target="b", rank=2, pred_top=["a", "b"], failure_type="cpu",
             pred_category="cpu", remediated=True),
        dict(target="b", rank=1, pred_top=["b"], failure_type="cpu",
             pred_category="memory", remediated=False),
        dict(target="b", rank=None, pred_top=[], failure_type="cpu",
             pred_category=None),
    ]
    result = summarize(rows)
    assert result["recovery_given_correct_top1"]["rate"] == 0
    assert result["recovery_given_correct_category"]["rate"] == 1
    assert result["recovery"]["n"] == 2
    assert result["n_category_missing"] == 1


def test_rejects_rank_that_disagrees_with_saved_prefix():
    with pytest.raises(ValueError):
        summarize([dict(target="b", rank=1, pred_top=["a", "b"], failure_type="cpu")])
