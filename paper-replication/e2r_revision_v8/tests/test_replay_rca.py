import pytest

from rca_bench.interface import MultimodalContext, RCAResult, RootCauseCandidate
from rca_bench.measurement import measure
from scripts.replay_rca import replay


class CheckingAdapter:
    def diagnose(self, context):
        assert context.services == ["a", "b"]
        assert "target" not in context.raw
        context.services.append("local-mutation")
        return RCAResult(ranking=[RootCauseCandidate("a", 1)])


def test_replay_isolates_mutation_and_keeps_labels_outside_context():
    ctx = MultimodalContext("ns", "app", ["a", "b"])
    data = {"hide_artifacts": True, "incidents": [
        {"rank": 1, "target": "a", "failure_type": "cpu-stress", "context": ctx.to_dict()}]}
    result = replay(data, {"first": CheckingAdapter(), "second": CheckingAdapter()})
    assert result["n_paired"] == 1
    assert data["incidents"][0]["context"]["services"] == ["a", "b"]
    assert result["metrics"]["first"]["ac_at_k"]["AC@1"] == 1


def test_replay_rejects_missing_context_instead_of_synthesizing_it():
    with pytest.raises(ValueError, match="Missing saved context"):
        replay({"hide_artifacts": True, "incidents": [{"rank": 1}]}, {})


def test_stage_time_includes_exception_path(monkeypatch):
    times = iter([10, 12.5])
    monkeypatch.setattr("rca_bench.measurement.perf_counter", lambda: next(times))
    timings = {}
    with pytest.raises(RuntimeError):
        with measure(timings, "execution"):
            raise RuntimeError("failed execution")
    assert timings == {"execution": 2.5}
