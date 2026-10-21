import json

import pytest

from methods.E2R.intervention.__main__ import main
from methods.E2R.intervention.schema import EvidenceGraph, Fact, input_bundle


def bundle():
    return {"schema": "e2r_intervention_v1", "graph": {"at": 100.0,
            "entities": [{"id": "service", "role": "service"}],
            "facts": [], "relations": [], "goals": ["service"]},
            "plans": [{"id": "observe_only", "actions": []}]}


def test_real_entry_point_writes_check_package(tmp_path):
    source, out = tmp_path / "input.json", tmp_path / "package.json"
    source.write_text(json.dumps(bundle()))
    assert main(["check", "--input", str(source), "--out", str(out)]) == 0
    row = json.loads(out.read_text())
    assert row["input_valid"] and row["decision"] == "abstain"
    assert row["empirical_effects"]["predictions"] is None
    assert row["observed_verification"]["status"] == "not_executed"
    with pytest.raises(FileExistsError):
        main(["check", "--input", str(source), "--out", str(out)])


def test_future_and_outcome_labels_cannot_enter_input():
    x = bundle()
    x["outcome"] = {"business_recovered": True}
    with pytest.raises(ValueError, match="separate"):
        input_bundle(x)
    x = bundle()
    x["graph"]["facts"] = [dict(id="f", entity="service", variable="healthy", value=True,
                                  observed_at=101, ttl=10, source="probe")]
    with pytest.raises(ValueError, match="future"):
        input_bundle(x)


def test_stale_missing_and_model_facts_are_not_current_truth():
    graph, _ = input_bundle(bundle())
    assert graph.read("service", "healthy") is None
    graph.facts.append(Fact("old", "service", "healthy", True, 80, 10, "probe"))
    assert graph.read("service", "healthy") is None
    graph.facts.append(Fact("pred", "service", "healthy", True, 100, 10, "model", "learned"))
    assert graph.read("service", "healthy") is None


def test_newer_missing_read_does_not_revive_older_success():
    graph, _ = input_bundle(bundle())
    graph.facts = [Fact("a", "service", "healthy", True, 90, 20, "probe"),
                   Fact("b", "service", "healthy", None, 99, 20, "probe")]
    assert graph.read("service", "healthy") is None
