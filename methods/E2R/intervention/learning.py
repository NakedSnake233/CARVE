"""Offline fitting and auditable prediction; no parameter updates during an incident."""
from __future__ import annotations

import copy
import json
import math
import random
import time
from collections import Counter, defaultdict
from pathlib import Path

import torch
from torch.nn import functional as F

from .data import distinguish_pairs, group_key, validate_record, validate_split
from .model import ActionConditionedGraph, FEATURES, plan_signature, tensorize
from .schema import EFFECTS, number


def targets(row):
    values = [row["outcome"]["effects"][k] for k in EFFECTS]
    return torch.tensor([float(v) if v is not None else 0.0 for v in values]), \
        torch.tensor([v is not None for v in values], dtype=torch.bool)


def effect_loss(logits, target, mask):
    return F.binary_cross_entropy_with_logits(logits[mask], target[mask]) if mask.any() else logits.sum() * 0


def fit(rows, split, out_dir, *, seeds=(42, 43, 44), epochs=40, hidden=64, variant="graph",
        objective="effect_distinguish", learning_rate=0.003, distinguish_weight=0.2,
        compact_weight=0.01, minimum_repeats=5, allow_synthetic=False, run_metadata=None):
    if objective not in ("effect_distinguish", "effect", "diagnosis", "state") or epochs < 2:
        raise ValueError("invalid training objective or epoch budget")
    validate_split(rows, split)
    for r in rows:
        validate_record(r, allow_synthetic=allow_synthetic)
    if len({r["source"] for r in rows}) != 1:
        raise ValueError("mixed data provenance")
    train = [r for r in rows if group_key(r) in split["train"] and "input" in r]
    validation = [r for r in rows if group_key(r) in split["validation"] and "input" in r]
    if not train or not validation:
        raise ValueError("missing training or validation groups")
    if objective == "diagnosis" and any(type(r.get("diagnosis_label")) is not int or not 0 <= r["diagnosis_label"] < 8 for r in train):
        raise ValueError("diagnosis baseline needs evaluator-provided labels, separate from input")
    state_indices = [i for i,r in enumerate(train) if len(r.get("state_targets", [])) == len(EFFECTS)
                     and all(number(x) for x in r["state_targets"])]
    if objective == "state" and not state_indices:
        raise ValueError("state baseline needs separately observed post-state targets")
    path = Path(out_dir)
    path.mkdir(parents=True, exist_ok=False)
    torch.set_num_threads(min(2, torch.get_num_threads()))
    train_items = [tensorize(*validate_record(r, allow_synthetic=True)) for r in train]
    val_items = [tensorize(*validate_record(r, allow_synthetic=True)) for r in validation]
    train_targets, val_targets = [targets(r) for r in train], [targets(r) for r in validation]
    pairs = distinguish_pairs(train, minimum_repeats) if objective == "effect_distinguish" else []
    if not any(mask.any() for _, mask in train_targets):
        raise ValueError("training has no observed effect labels")
    support = defaultdict(lambda: [0] * len(EFFECTS))
    ranges = {}
    for row in train:
        graph, plan = validate_record(row, allow_synthetic=True)
        signature = plan_signature(graph, plan)
        for i, key in enumerate(EFFECTS):
            support[signature][i] += int(row["outcome"]["effects"][key] is not None)
        for fact in graph.facts:
            if number(fact.value):
                low, high = ranges.get(fact.variable, (fact.value, fact.value))
                ranges[fact.variable] = (min(low, fact.value), max(high, fact.value))
    config = {"schema": "e2r_intervention_training_v1", "source": rows[0]["source"],
              "action_signature_version": 2,
              "train_attempts": sum(group_key(r) in split["train"] for r in rows),
              "seeds": list(seeds), "epochs": epochs, "objective": objective, "variant": variant,
              "hidden": hidden, "features": list(FEATURES), "effects": list(EFFECTS), "split": split,
              "train_episodes": len(train), "validation_episodes": len(validation),
              "observed_auxiliary_state_episodes": len(state_indices),
              "pair_count": len(pairs), "minimum_repeats": minimum_repeats,
              'pair_kind_counts':dict(Counter(p['kind'] for p in pairs)),
              'protocol_matching_for_pairs':True,
              'training_protocol_strata':dict(Counter(r.get('protocol_stratum','legacy_unspecified') for r in train)),
              'run_metadata':copy.deepcopy(run_metadata or {}),
              "support": dict(support), "ranges": ranges, "distinguish_weight": distinguish_weight,
              "compact_weight": compact_weight, "learning_rate": learning_rate,
              "claim_status": "engineering_only" if rows[0]["source"] == "synthetic" else "unevaluated",
              "uncertainty_semantics": "ensemble spread and validation residuals; not a formal safety bound"}
    (path / "training.json").write_text(json.dumps(config, indent=2) + "\n")
    (path / "pairs.json").write_text(json.dumps(pairs, indent=2) + "\n")
    def progress(value):
        value.update(updated_at=time.time(), planned_seeds=list(seeds), planned_epochs=epochs)
        temporary=path/'progress.json.tmp'
        temporary.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
        temporary.replace(path/'progress.json')
    progress({'status':'running','phase':'initialized','completed_seeds':0})
    summaries = []
    for seed in seeds:
        random.seed(seed)
        torch.manual_seed(seed)
        model = ActionConditionedGraph(hidden=hidden, variant=variant)
        optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
        best, best_state, history = float("inf"), None, []
        for epoch in range(epochs):
            epoch_started=time.perf_counter()
            pretraining = objective in ("diagnosis", "state") and epoch < epochs // 2
            if objective in ("diagnosis", "state") and epoch == epochs // 2:
                for name, parameter in model.named_parameters():
                    parameter.requires_grad_(name.startswith("effect_head"))
            model.train()
            outputs = [model(item) for item in train_items]
            if pretraining and objective == "diagnosis":
                loss = torch.stack([F.cross_entropy(o["diagnosis"].unsqueeze(0), torch.tensor([r["diagnosis_label"]]))
                                    for o, r in zip(outputs, train)]).mean()
            elif pretraining:
                loss = torch.stack([F.mse_loss(outputs[i]["state"], torch.tensor(train[i]["state_targets"], dtype=torch.float32))
                                    for i in state_indices]).mean()
            else:
                loss = torch.stack([effect_loss(o["logits"], y, m) for o, (y, m) in zip(outputs, train_targets)]).mean()
            pair_loss = loss * 0
            if pairs:
                distances = []
                for pair in pairs:
                    left = F.normalize(outputs[pair["left"]]["embedding"], dim=0)
                    right = F.normalize(outputs[pair["right"]]["embedding"], dim=0)
                    distances.append((torch.linalg.vector_norm(left-right)/2 - pair["distance"]) ** 2)
                pair_loss = torch.stack(distances).mean()
            compact = torch.stack([o["compact_loss"] for o in outputs]).mean()
            total = loss + distinguish_weight * pair_loss + compact_weight * compact
            if not torch.isfinite(total):
                raise RuntimeError('non-finite training loss; preserve run and inspect before retry')
            optimizer.zero_grad()
            total.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5)
            optimizer.step()
            model.eval()
            with torch.no_grad():
                val = torch.stack([effect_loss(model(item)["logits"], y, m)
                                   for item, (y, m) in zip(val_items, val_targets)]).mean().item()
            history.append({"epoch": epoch, "loss": total.item(), "effect_or_aux_loss": loss.item(),
                            "distinguish_loss": pair_loss.item(), "compact_loss": compact.item(),
                            "validation_loss": val, "phase": "representation_pretrain" if pretraining else "effect",
                            'epoch_seconds':time.perf_counter()-epoch_started})
            if not math.isfinite(val):
                raise RuntimeError('non-finite validation loss; no checkpoint selected')
            progress({'status':'running','phase':history[-1]['phase'],'seed':seed,'epoch':epoch+1,
                      'completed_seeds':len(summaries),'last_epoch':history[-1]})
            print(json.dumps({'seed':seed,**history[-1]}),flush=True)
            if not pretraining and val < best:
                best, best_state = val, copy.deepcopy(model.state_dict())
        torch.save({"state_dict": best_state, "model_config": model.config, "features": list(FEATURES)},
                   path / f"seed{seed}.pt")
        summaries.append({"seed": seed, "best_validation_loss": best})
        (path / f"seed{seed}_history.json").write_text(json.dumps(history, indent=2) + "\n")
    (path / "summary.json").write_text(json.dumps(summaries, indent=2) + "\n")
    predictor = EffectPredictor(path)
    def residuals(candidate):
        errors = defaultdict(list)
        for row in validation:
            graph, plan = validate_record(row, allow_synthetic=True)
            prediction = candidate.predict(graph, plan)
            for effect in EFFECTS:
                observed = row["outcome"]["effects"][effect]
                predicted = prediction["probabilities"][effect]
                if observed is not None and predicted is not None:
                    errors[effect].append(abs(float(observed) - predicted))
        # Descriptive validation residual, not a conformal/production guarantee.
        return {key: sorted(values)[min(len(values)-1, int(0.9*len(values)))] for key, values in errors.items()}
    config["calibration"] = residuals(predictor)
    config["seed_calibration"] = {str(seed): residuals(EffectPredictor(path, seeds=[seed])) for seed in seeds}
    (path / "training.json").write_text(json.dumps(config, indent=2) + "\n")
    progress({'status':'complete','phase':'calibrated','completed_seeds':len(summaries)})
    return {"training": config, "seeds": summaries}


class EffectPredictor:
    def __init__(self, directory, seeds=None):
        torch.set_num_threads(min(2, torch.get_num_threads()))
        directory = Path(directory)
        self.metadata = json.loads((directory / "training.json").read_text())
        if self.metadata.get("action_signature_version") != 2:
            raise ValueError("checkpoint predates exact intervention support grouping; retrain")
        if self.metadata["features"] != list(FEATURES):
            raise ValueError("checkpoint feature schema mismatch")
        self.models = []
        self.selected_seeds = list(self.metadata["seeds"] if seeds is None else seeds)
        if not self.selected_seeds or not set(self.selected_seeds) <= set(self.metadata["seeds"]):
            raise ValueError("requested predictor seed is not in this trained bundle")
        if len(self.selected_seeds) != 1 and self.selected_seeds != self.metadata["seeds"]:
            raise ValueError("calibration is registered for individual seeds or the complete ensemble")
        self.metadata["prediction_seeds"] = self.selected_seeds
        if len(self.selected_seeds) == 1:
            self.metadata["calibration"] = self.metadata.get("seed_calibration", {}).get(str(self.selected_seeds[0]), {})
        for seed in self.selected_seeds:
            blob = torch.load(directory / f"seed{seed}.pt", map_location="cpu", weights_only=True)
            model = ActionConditionedGraph(**blob["model_config"])
            model.load_state_dict(blob["state_dict"])
            self.models.append(model.eval())

    def predict(self, graph, plan):
        item = tensorize(graph, plan)
        with torch.no_grad():
            outputs = [model(item) for model in self.models]
            probabilities = torch.stack([o["logits"].sigmoid() for o in outputs])
        signature = plan_signature(graph, plan)
        counts = self.metadata["support"].get(signature, [0] * len(EFFECTS))
        dimension_counts = [sum(v[i] for v in self.metadata["support"].values()) for i in range(len(EFFECTS))]
        mean, spread = probabilities.mean(0).tolist(), probabilities.std(0, unbiased=False).tolist()
        outside = []
        for fact in graph.facts:
            if number(fact.value) and fact.variable in self.metadata["ranges"]:
                low, high = self.metadata["ranges"][fact.variable]
                margin = max(1e-6, (high-low) * 0.2)
                if not low-margin <= fact.value <= high+margin:
                    outside.append(fact.id)
        return {"probabilities": {k: v if n else None for k, v, n in zip(EFFECTS, mean, dimension_counts)},
                "raw_probabilities": dict(zip(EFFECTS, mean)),
                "globally_trained_dimensions": dict(zip(EFFECTS, [n > 0 for n in dimension_counts])),
                "spread": dict(zip(EFFECTS, spread)), "support": dict(zip(EFFECTS, counts)),
                "source": self.metadata["source"], "unseen_signature": signature not in self.metadata["support"],
                "calibration": self.metadata.get("calibration", {}),
                "outside_training_range": outside, "model_count": len(self.models),
                "selected_seeds": self.selected_seeds,
                "uncertainty_semantics": self.metadata["uncertainty_semantics"],
                "graph_sizes": [{"retained": o["kept_nodes"], "total": o["total_nodes"], "edges": o["kept_edges"]}
                                for o in outputs]}
