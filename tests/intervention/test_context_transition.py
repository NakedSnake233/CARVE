"""Engineering fixtures exercise learning and boundaries; not native evidence."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from methods.E2R.intervention import context_transition as ct
from scripts.e2r import fit_context_transition as fit_cli
from scripts.e2r import evaluate_context_transition as eval_cli


def features(value=1., **updates):
    result = {name: value for name in ct.FEATURE_NAMES}
    result.update(updates)
    return result


def training_rows():
    rows = []
    # XOR-like action interaction: the same observed state needs a different arm.
    for operation in ("restore_selector", "rollback_version"):
        for state in (0., 1.):
            for repeat in range(4):
                recovered = bool(state) == (operation == "restore_selector")
                rows.append({"id": f"fixture-{operation}-{state}-{repeat}", "source": "live",
                             "labels_verified": True, "stage": "train", "fault": "engineering_fixture",
                             "status": "observed", "gate_status": "eligible", "operation": operation,
                             "features": features(current_route_matches=state), "success": recovered,
                             "safety": True, "execution_seconds": 2.+state, "timings_seconds": {}})
    return rows


@pytest.fixture(scope="module")
def model():
    return ct.fit_context_model(training_rows())


def test_same_operation_distinguishes_observed_states_and_coarse_cannot(model):
    positive = model.predict_features(features(current_route_matches=1.), "restore_selector")
    negative = model.predict_features(features(current_route_matches=0.), "restore_selector")
    reverse = model.predict_features(features(current_route_matches=1.), "rollback_version")
    assert positive["success"] > .7 and negative["success"] < .3
    assert reverse["success"] < .3
    assert positive["status"] == "ok"
    assert model.predict_features(features(current_route_matches=0.), "restore_selector", blind=True)["success"] == model.predict_features(features(current_route_matches=1.), "restore_selector", blind=True)["success"]


def test_unknown_support_and_optional_missing_are_explicit(model):
    absent = model.predict_features({}, "restore_selector")
    assert absent["status"] == "unknown" and absent["success"] is None and absent["utility"] == -1.
    unsupported = model.predict_features(features(), "scale")
    assert unsupported["support"] == 0 and unsupported["success"] is None
    partial = features(target_capacity=None, reference_load=None)
    forecast = model.predict_features(partial, "restore_selector")
    assert forecast["status"] == "ok" and forecast["missing_features"] == ["target_capacity", "reference_load"]
    partial["projected_routed_replicas"] = None
    assert model.predict_features(partial, "restore_selector")["status"] == "unknown"
    # Known zero and false are values; they do not become absent values.
    assert model.predict_features(features(0.), "restore_selector")["status"] == "ok"
    with pytest.raises(ValueError, match="unregistered"):
        model.predict_features({**features(), "fault": 1}, "restore_selector")
    with pytest.raises(ValueError, match="finite"):
        model.predict_features(features(entry_error_rate=float("nan")), "restore_selector")


def test_schema_reload_and_fixed_three_seed_predictions(model, tmp_path):
    target = tmp_path/"model.json"
    ct.save_json(target, model.metadata)
    reloaded = ct.ContextTransitionModel.load(target)
    assert reloaded.metadata == model.metadata
    expected = model.predict_features(features(), "restore_selector")
    actual = reloaded.predict_features(features(), "restore_selector")
    expected.pop("prediction_seconds")
    actual.pop("prediction_seconds")
    assert actual == expected
    bad = copy.deepcopy(model.metadata)
    bad["feature_names"].reverse()
    with pytest.raises(ValueError, match="schema"):
        ct.ContextTransitionModel(bad)
    bad = copy.deepcopy(model.metadata)
    bad["heads"]["context"]["success"].pop()
    with pytest.raises(ValueError, match="seed"):
        ct.ContextTransitionModel(bad)


def test_absent_paths_are_observed_contexts_not_unknown_effect_labels(model):
    absent = features(projected_route_matches=0., projected_routed_replicas=None,
                      projected_goal_owner_match=None, projected_required_store_replicas=None,
                      projected_required_store_route_matches=None)
    forecast = model.predict_features(absent, "restore_selector")
    assert forecast["status"] == "ok" and forecast["success"] is not None
    dormant = {**absent, "projected_route_matches": 1., "projected_routed_replicas": 0.}
    assert model.predict_features(dormant, "restore_selector")["status"] == "ok"
    no_store_route = {**dormant, "projected_routed_replicas": 1., "projected_required_store_route_matches": 0.}
    assert model.predict_features(no_store_route, "restore_selector")["status"] == "ok"
    genuinely_missing = {**absent, "projected_route_matches": None}
    assert model.predict_features(genuinely_missing, "restore_selector")["status"] == "unknown"
    rows = training_rows()[:1]
    rows[0].update(features=absent, gate_status="blocked", success=True)
    report = ct.evaluate_rows(model, rows)
    assert report["overall"]["context"]["raw"]["success"]["positives"] == 1


def test_unknown_labels_keep_denominators_without_becoming_zero(model):
    rows = training_rows()[:2]
    rows[0].update(success=None, safety=None, execution_seconds=None, status="rejected", gate_status="blocked")
    rows[1]["gate_status"] = "blocked"
    report = ct.evaluate_rows(model, rows)
    raw = report["overall"]["context"]["raw"]["success"]
    gated = report["overall"]["context"]["after_structural_gate"]["success"]
    assert raw["total"] == 2 and raw["known_labels"] == 1 and raw["missing_labels"] == 1
    assert raw["scored"] == 1 and gated["scored"] == 0
    assert gated["known_labels"] == 1 and gated["known_but_prediction_unknown"] == 1
    assert report["overall"]["structural_gate"] == {"blocked": 2}


def test_missing_api_cost_is_null_and_measured_zero_stays_zero(model):
    rows = training_rows()[:2]
    report = ct.evaluate_rows(model, rows)
    for section in (report["overall"], report["by_fault"]["engineering_fixture"]):
        cost = section["cost"]
        assert cost["api_calls"] is None and cost["api_seconds"] is None
        assert cost["api_calls_observed_sum"] is None
        assert cost["api_diagnostics_known_attempts"] == 0
        assert cost["api_diagnostics_missing_attempts"] == 2
    for row in rows:
        row["api_diagnostics"] = {"api_calls": 0, "api_seconds": 0.}
    cost = ct.evaluate_rows(model, rows)["overall"]["cost"]
    assert cost["api_calls"] == 0 and cost["api_seconds"] == 0.
    assert cost["api_diagnostics_known_attempts"] == 2
    assert cost["api_diagnostics_missing_attempts"] == 0
    rows[0]["api_diagnostics"] = {"api_calls": 3, "api_seconds": 1.5}
    rows[1]["api_diagnostics"] = {"api_calls": None}
    cost = ct.evaluate_rows(model, rows)["overall"]["cost"]
    assert cost["api_calls"] is None and cost["api_seconds"] is None
    assert cost["api_calls_observed_sum"] == 3 and cost["api_seconds_observed_sum"] == 1.5
    assert cost["api_calls_known_attempts"] == cost["api_seconds_known_attempts"] == 1
    assert cost["api_calls_missing_attempts"] == cost["api_seconds_missing_attempts"] == 1


def test_only_train_rows_enter_fit_and_unknown_targets_have_no_default():
    rows = training_rows()
    rows[0]["stage"] = "test"
    with pytest.raises(ValueError, match="only train"):
        ct.fit_context_model(rows)
    rows = training_rows()
    for row in rows:
        row["safety"] = None
    fitted = ct.fit_context_model(rows)
    forecast = fitted.predict_features(features(), "restore_selector")
    assert forecast["status"] == "unknown" and forecast["safety"] is None and forecast["utility"] == -1.
    assert fitted.metadata["training_known"]["safety"] == 0
    assert fitted.metadata["heads"]["coarse"]["safety"] is None


def make_queue(tmp_path, stage, *, missing=False, gate="eligible"):
    directory = tmp_path/(stage+"_v1")
    directory.mkdir()
    campaign = directory/"campaign"
    campaign.mkdir()
    config = directory/"config.json"
    ct.save_json(config, {"scheduled_jobs": [{"arm": "rollback"}, {"arm": "switch"}]})
    ct.save_json(directory/"PREREGISTRATION.json", {"stage": stage, "planned_positions": 2,
                 "assignments": [{"id": stage, "out": str(campaign), "config": str(config), "fault": "fixture"}]})
    if not missing:
        (campaign/"episode-000000").mkdir()
        raw = {"episode_id": "episode-000000", "source": "live", "chosen_plan": "rollback", "status": "observed",
               "action_started_at": 20., "outcome": {"effects": {"business_recovered": True, "non_target_safe": False}},
               "execution": {"status": "executed", "timings_seconds": {"write_0": 1., "controller_0": 2., "contract": 9.}}}
        (campaign/"episodes.jsonl").write_text(json.dumps(raw)+"\n")
        ct.save_json(campaign/"episode-000000/action_context_candidates.json", {
            "schema": "action_context_candidates_v1", "captured_at": 19., "candidates": {
                "rollback": {"status": gate, "features": features(current_route_matches=0.)},
                "switch": {"status": "eligible", "features": features(current_route_matches=1.)}}})
    return directory


def validation_stub(row):
    assert row["source"] == "live"
    return None, SimpleNamespace(actions=[SimpleNamespace(operation="rollback_version")])


def test_loader_only_selected_native_labels_and_preserves_not_started(tmp_path, monkeypatch):
    monkeypatch.setattr(ct, "validate_record", validation_stub)
    queue = make_queue(tmp_path, "training", gate="blocked")
    rows, source = ct.load_queue(queue, expected_stage="train")
    assert len(rows) == 2 and rows[0]["features"]["current_route_matches"] == 0.
    assert rows[0]["success"] is True  # Blocked is a structural claim, never a negative label.
    assert rows[0]["safety"] is False and rows[0]["execution_seconds"] == 3.
    assert rows[1]["status"] == "not_started" and rows[1]["success"] is None
    assert source["stage"] == "train"
    raw_path = queue/"campaign/episodes.jsonl"
    raw = json.loads(raw_path.read_text())
    raw["source"] = "synthetic"
    raw_path.write_text(json.dumps(raw)+"\n")
    monkeypatch.setattr(ct, "validate_record", lambda _: (None, None))
    with pytest.raises(ValueError, match="native provenance"):
        ct.load_queue(queue)


def test_context_temporal_boundary_and_native_validation_are_enforced(tmp_path, monkeypatch):
    queue = make_queue(tmp_path, "training")
    calls = []
    def validator(raw):
        calls.append(raw["episode_id"])
        return validation_stub(raw)
    monkeypatch.setattr(ct, "validate_record", validator)
    path = queue/"campaign/episode-000000/action_context_candidates.json"
    package = json.loads(path.read_text())
    package["captured_at"] = 21.
    ct.save_json(path, package)
    with pytest.raises(ValueError, match="post-action"):
        ct.load_queue(queue)
    assert calls == ["episode-000000"]


def test_queue_split_rejected_before_opening_any_labels(tmp_path, monkeypatch):
    queue = make_queue(tmp_path, "test")
    def forbidden(*args, **kwargs):
        raise AssertionError("test labels opened during fit")
    monkeypatch.setattr(ct, "validate_record", forbidden)
    with pytest.raises(ValueError, match="permitted split"):
        ct.load_queue(queue, expected_stage="train")
    with pytest.raises(ValueError, match="permitted split"):
        fit_cli.run(queue, queue, tmp_path/"model")


def test_fit_freezes_before_validation_and_never_reads_test(tmp_path, monkeypatch):
    train = make_queue(tmp_path, "training")
    validation = make_queue(tmp_path, "validation")
    test = make_queue(tmp_path, "test")
    plan = Path(__file__).resolve().parents[2]/"enhancement/method-stage/context_repair_20261003/MODEL_PLAN.json"
    ct.save_json(tmp_path/"MODEL_PLAN.json", json.loads(plan.read_text()))
    monkeypatch.setattr(ct, "validate_record", validation_stub)
    original = fit_cli.load_queue
    out = tmp_path/"frozen"
    opened = []
    def checked(path, **kwargs):
        opened.append(Path(path).parent.name)
        if "validation" in str(path):
            assert (out/"model.json").exists()
        assert "test_v1" not in str(path)
        return original(path, **kwargs)
    monkeypatch.setattr(fit_cli, "load_queue", checked)
    result = fit_cli.run(train, validation, out)
    assert opened == ["training_v1", "validation_v1"]
    assert result["test_evaluated"] is False
    assert result["status"] == "complete"
    assert result["validation_used_for_fitting"] is False
    frozen_hash = ct.file_digest(out/"model.json")
    evaluated = eval_cli.run(test, out/"model.json", tmp_path/"test_report")
    assert evaluated["stage"] == "test" and evaluated["model_updated"] is False
    assert evaluated["status"] == "complete"
    assert ct.file_digest(out/"model.json") == frozen_hash
    with pytest.raises(ValueError, match="already frozen"):
        fit_cli.run(train, validation, out)


def test_test_evaluation_requires_frozen_validation_report(model, tmp_path):
    queue = make_queue(tmp_path, "test")
    model_path = tmp_path/"model.json"
    ct.save_json(model_path, model.metadata)
    with pytest.raises(ValueError, match="completed frozen"):
        eval_cli.run(queue, model_path, tmp_path/"report")
