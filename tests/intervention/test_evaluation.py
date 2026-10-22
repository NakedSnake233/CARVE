from methods.E2R.intervention.data import grouped_split
from methods.E2R.intervention.evaluation import ManualEffects, evaluate, summarize_decisions
from methods.E2R.intervention.examples import example_bundle, synthetic_records
from methods.E2R.intervention.contracts import Policy
from methods.E2R.intervention.schema import EFFECTS


def test_unknown_outcomes_produce_bounds_not_optimistic_success():
    outcomes = {k: {"successes": 1, "known": 1, "unknown": 1, "attempts": 2, "mean": 1.0} for k in EFFECTS}
    row = {"score": 0.9, "matching_arm_present": True, "outcomes": outcomes}
    report = summarize_decisions([row], 0.5)
    assert report["population_recovery_lower"] == 0.5
    assert report["population_recovery_upper"] == 1.0
    assert report["risk_lower"] == 0 and report["risk_upper"] == 0.5


def test_missing_alternative_arm_is_not_an_observed_counterfactual():
    outcomes = {k: {"successes": 0, "known": 0, "unknown": 0, "attempts": 0, "mean": None} for k in EFFECTS}
    row = {"score": 0.9, "matching_arm_present": False, "outcomes": outcomes}
    report = summarize_decisions([row], 0.5)
    assert report["recovery_among_measured"] is None
    assert report["population_recovery_lower"] == 0 and report["population_recovery_upper"] == 1


def test_evaluation_provenance_and_validation_threshold_source():
    rows = synthetic_records(groups=8, repeats=5)
    _, policy = example_bundle()
    result = evaluate(rows, grouped_split(rows), Policy.from_dict(policy), ManualEffects())
    assert result["claim_status"] == "engineering_only"
    assert all(r["threshold_source"] == "validation_only" for r in result["risk_coverage"])
    assert "not a live rollout" in result["policy_value_scope"]
