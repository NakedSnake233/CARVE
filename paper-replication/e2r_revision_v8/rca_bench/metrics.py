"""RCA evaluation metrics: AC@k (Accuracy@k) and MRR, plus category accuracy.

Definitions (root-cause = a single ground-truth service per incident):

* **rank**: 1-based position of the true service in the predicted ranking
  (``None`` if absent).
* **AC@k** (a.k.a. A@k / PR@k / top-k accuracy): fraction of incidents whose
  true service appears within the first ``k`` predictions.
* **MRR** (Mean Reciprocal Rank): mean of ``1/rank`` over incidents (``0`` when
  the true service is absent from the ranking).
* **category accuracy**: fraction of incidents whose predicted failure_category
  equals the true category (over incidents where a category was predicted).

Pure stdlib; no cluster/LLM. Fully unit-testable offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence


@dataclass
class IncidentEval:
    """One scored incident: ground truth + the algorithm's prediction."""
    incident_id: str
    failure_category: str            # ground-truth category (oracle, scoring only)
    true_service: str                # ground-truth root-cause service (oracle)
    predicted_ranking: List[str]     # ranked candidate services (algorithm output)
    predicted_category: Optional[str] = None

    def rank_of_truth(self) -> Optional[int]:
        for i, s in enumerate(self.predicted_ranking):
            if s == self.true_service:
                return i + 1
        return None


def rank_of(true_service: str, ranking: Sequence[str]) -> Optional[int]:
    for i, s in enumerate(ranking):
        if s == true_service:
            return i + 1
    return None


def accuracy_at_k(evals: Sequence[IncidentEval], k: int) -> float:
    if not evals:
        return 0.0
    hits = 0
    for e in evals:
        r = e.rank_of_truth()
        if r is not None and r <= k:
            hits += 1
    return hits / len(evals)


def mrr(evals: Sequence[IncidentEval]) -> float:
    if not evals:
        return 0.0
    total = 0.0
    for e in evals:
        r = e.rank_of_truth()
        if r is not None:
            total += 1.0 / r
    return total / len(evals)


def category_accuracy(evals: Sequence[IncidentEval]) -> Optional[float]:
    scored = [e for e in evals if e.predicted_category is not None]
    if not scored:
        return None
    correct = sum(1 for e in scored if e.predicted_category == e.failure_category)
    return correct / len(scored)


@dataclass
class RCAMetrics:
    count: int
    ac_at_k: Dict[int, float]
    mrr: float
    category_accuracy: Optional[float]
    by_category: Dict[str, "RCAMetrics"] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "count": self.count,
            "ac_at_k": {f"AC@{k}": round(v, 4) for k, v in self.ac_at_k.items()},
            "mrr": round(self.mrr, 4),
            "category_accuracy": (round(self.category_accuracy, 4)
                                  if self.category_accuracy is not None else None),
            "by_category": {c: m.to_dict() for c, m in self.by_category.items()},
        }


def evaluate(evals: Sequence[IncidentEval], ks: Sequence[int] = (1, 3, 5),
             per_category: bool = True) -> RCAMetrics:
    """Aggregate overall (and optionally per-failure-category) RCA metrics."""
    overall = RCAMetrics(
        count=len(evals),
        ac_at_k={k: accuracy_at_k(evals, k) for k in ks},
        mrr=mrr(evals),
        category_accuracy=category_accuracy(evals),
    )
    if per_category:
        cats: Dict[str, List[IncidentEval]] = {}
        for e in evals:
            cats.setdefault(e.failure_category, []).append(e)
        overall.by_category = {
            c: RCAMetrics(
                count=len(group),
                ac_at_k={k: accuracy_at_k(group, k) for k in ks},
                mrr=mrr(group),
                category_accuracy=category_accuracy(group),
            )
            for c, group in sorted(cats.items())
        }
    return overall


def format_report(metrics: RCAMetrics, ks: Sequence[int] = (1, 3, 5),
                  title: str = "RCA metrics") -> str:
    """Human-readable table of overall + per-category metrics."""
    lines = [f"=== {title} (n={metrics.count}) ==="]
    head = "  " + "".join(f"AC@{k:<5}" for k in ks) + "MRR     cat-acc"
    lines.append(head)

    def row(label: str, m: RCAMetrics) -> str:
        cells = "".join(f"{m.ac_at_k.get(k, 0.0):<7.3f}" for k in ks)
        cat = f"{m.category_accuracy:.3f}" if m.category_accuracy is not None else "  n/a"
        return f"{label:<22}{cells}{m.mrr:<8.3f}{cat}"

    lines.append(row("OVERALL", metrics))
    for c, m in metrics.by_category.items():
        lines.append(row(f"  {c} (n={m.count})", m))
    return "\n".join(lines)
