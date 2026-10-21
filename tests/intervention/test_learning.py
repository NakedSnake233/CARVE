import copy
import json

import pytest
import torch

from methods.E2R.intervention.data import distinguish_pairs, grouped_split, validate_record, validate_split
from methods.E2R.intervention.examples import example_bundle, synthetic_records
from methods.E2R.intervention.learning import EffectPredictor, fit
from methods.E2R.intervention.model import ActionConditionedGraph, FEATURES, tensorize
from methods.E2R.intervention.schema import input_bundle


def test_synthetic_provenance_and_group_split_are_enforced():
    records = synthetic_records(groups=5)
    with pytest.raises(ValueError, match="synthetic"):
        validate_record(records[0])
    split = grouped_split(records)
    assert not set(split["train"]) & set(split["test"])
    split["test"].append(split["train"][0])
    with pytest.raises(ValueError, match="overlaps"):
        validate_split(records, split)
    records[0]["action_started_at"] = 99
    with pytest.raises(ValueError, match="temporal"):
        validate_record(records[0], allow_synthetic=True)


def test_distinguish_labels_need_repeated_comparable_execution():
    assert distinguish_pairs(synthetic_records(groups=1, repeats=1)) == []
    pairs = distinguish_pairs(synthetic_records(groups=1, repeats=5))
    assert pairs and any(p["kind"] == "different" for p in pairs)
    assert all(p["left_repeats"] >= 5 and p["right_repeats"] >= 5 for p in pairs)


def test_effect_outcomes_and_resource_names_are_not_tensor_features():
    row = synthetic_records(groups=1)[0]
    graph, plan = validate_record(row, allow_synthetic=True)
    first = tensorize(graph, plan)
    row["outcome"]["effects"]["business_recovered"] = True
    graph2, plan2 = validate_record(row, allow_synthetic=True)
    assert torch.equal(first.x, tensorize(graph2, plan2).x)
    assert not any("name" in name or "uid" in name or "label" in name for name in FEATURES)


def test_encoder_is_equivariant_to_resource_renaming():
    original, _ = example_bundle()
    renamed = copy.deepcopy(original)
    mapping = {n["id"]: f"renamed-{i}" for i, n in enumerate(reversed(renamed["graph"]["entities"]))}
    for entity in renamed["graph"]["entities"]:
        entity["id"] = mapping[entity["id"]]
        entity["name"] = "unseen-" + entity["name"]
    for fact in renamed["graph"]["facts"]:
        fact["entity"] = mapping[fact["entity"]]
    for relation in renamed["graph"]["relations"]:
        relation["source"], relation["target"] = mapping[relation["source"]], mapping[relation["target"]]
    renamed["graph"]["goals"] = [mapping[x] for x in renamed["graph"]["goals"]]
    for plan in renamed["plans"]:
        for action in plan["actions"]:
            action["target"] = mapping[action["target"]]
            if action["destination"]:
                action["destination"] = mapping[action["destination"]]
    torch.set_num_threads(2)
    torch.manual_seed(1)
    model = ActionConditionedGraph(hidden=8).eval()
    g1, p1 = input_bundle(original)
    g2, p2 = input_bundle(renamed)
    assert torch.allclose(model(tensorize(g1, p1[2]))["logits"], model(tensorize(g2, p2[2]))["logits"], atol=1e-6)


def test_mandatory_structure_survives_learned_sparsification_and_backprop():
    bundle, _ = example_bundle()
    graph, plans = input_bundle(bundle)
    item = tensorize(graph, plans[2])
    model = ActionConditionedGraph(hidden=8)
    output = model(item)
    output["logits"].sum().backward()
    assert model.input.weight.grad is not None
    with torch.no_grad():
        model.gate[-1].bias.fill_(-100)
    model.eval()
    retained = set(model(item)["kept_indices"].tolist())
    assert set(item.mandatory.nonzero().flatten().tolist()) <= retained
    assert len(retained) < len(item.x)


def test_real_training_checkpoint_reload_and_predictions(tmp_path):
    rows = synthetic_records(groups=5, repeats=5)
    split = grouped_split(rows)
    result = fit(rows, split, tmp_path / "model", epochs=2, hidden=8, seeds=(42,43), allow_synthetic=True)
    assert result["training"]["claim_status"] == "engineering_only"
    assert result["training"]["pair_count"] > 0
    model = EffectPredictor(tmp_path / "model")
    graph, plans = input_bundle(rows[0]["input"])
    prediction = model.predict(graph, plans[1])
    assert prediction["source"] == "synthetic"
    assert all(0 <= p <= 1 for p in prediction["probabilities"].values())
    assert prediction["support"]["business_recovered"] > 0
    single = EffectPredictor(tmp_path / "model", seeds=[43])
    assert single.metadata["prediction_seeds"] == [43]
    assert single.metadata["calibration"] == result["training"]["seed_calibration"]["43"]
    assert single.metadata != model.metadata
    unseen = copy.deepcopy(plans[2])
    from dataclasses import replace
    unseen = replace(unseen, actions=(replace(unseen.actions[0], parameters={"replicas":2}), *unseen.actions[1:]))
    generalization = single.predict(graph,unseen)
    assert generalization["unseen_signature"]
    assert generalization["support"]["business_recovered"] == 0
    assert generalization["probabilities"]["business_recovered"] is not None
    history = json.loads((tmp_path / "model/seed42_history.json").read_text())
    assert all(torch.isfinite(torch.tensor(h["loss"])) for h in history)
