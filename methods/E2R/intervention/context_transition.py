"""Small frozen state/action predictors trained from verified native interventions.

Only the numerical feature allowlist reaches the model. Fault/condition identifiers
are retained separately by the queue reader for reporting, never for prediction.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from collections import Counter
from pathlib import Path

# Set before numpy is imported by the standalone fit/evaluate entry points.
for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
              "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "2"

import numpy as np

from .action_context import FEATURE_NAMES
from .data import validate_record

SCHEMA = "context_transition_model_v1"
OPERATIONS = ("restore_selector", "rollback_version", "scale", "no_action")
SEEDS = (42, 43, 44)
ITERATIONS = 1200
LEARNING_RATE = 0.1
L2 = 0.02
EFFECTS = {"success": "business_recovered", "safety": "non_target_safe"}
CALIBRATION_EDGES = (0., .2, .4, .6, .8, 1.)
CORE_FEATURES = ("projected_route_matches", "projected_routed_replicas", "projected_goal_owner_match",
                 "projected_required_store_replicas", "projected_required_store_route_matches")
CORE_SUPPORT_RULE = "require route; when route exists require replicas; when replicas positive require store route; when store route exists require owner match and store replicas; descendants of known absent paths may be null"


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def feature_vector(features):
    """Preserve absence explicitly; reject feature drift and nonfinite values."""
    if not isinstance(features, dict):
        raise ValueError("features must be a mapping with the frozen feature names")
    extras = set(features) - set(FEATURE_NAMES)
    if extras:
        raise ValueError("unregistered context features: " + ", ".join(sorted(extras)))
    values, observed = [], []
    for name in FEATURE_NAMES:
        value = features.get(name)
        if value is not None and (not isinstance(value, (int, float, bool)) or not math.isfinite(value)):
            raise ValueError("feature must be finite numeric or null: " + name)
        values.append(0. if value is None else float(value))
        observed.append(value is not None)
    return np.asarray(values, dtype=float), np.asarray(observed, dtype=bool)


def _mask_key(observed):
    return "".join("1" if value else "0" for value in observed)


def _required_configuration(features):
    """Distinguish an absent path from unread configuration, without effect labels."""
    required = ["projected_route_matches"]
    if features.get(required[-1]) in (None, 0):
        return required
    required.append("projected_routed_replicas")
    replicas = features.get(required[-1])
    if replicas is None or replicas <= 0:
        return required
    required.append("projected_required_store_route_matches")
    if features.get(required[-1]) in (None, 0):
        return required
    return required + ["projected_goal_owner_match", "projected_required_store_replicas"]


def _sigmoid(value):
    return 1. / (1. + np.exp(-np.clip(value, -40., 40.)))


def _design(values, observed, operation, normalizer, blind):
    op = np.zeros(len(OPERATIONS))
    op[OPERATIONS.index(operation)] = 1.
    if blind:
        return np.concatenate(([1.], op))
    centered = np.where(observed, (values - np.asarray(normalizer["mean"])) /
                        np.asarray(normalizer["scale"]), 0.)
    state = np.concatenate((centered, (~observed).astype(float)))
    return np.concatenate(([1.], op, state, np.outer(op, state).ravel()))


def _logistic(x, y, seed):
    rng = np.random.default_rng(seed)
    weights = rng.normal(0., .01, x.shape[1])
    for _ in range(ITERATIONS):
        penalty = L2 * weights
        penalty[0] = 0.
        weights -= LEARNING_RATE * (x.T @ (_sigmoid(x @ weights) - y) / len(y) + penalty)
    return weights.tolist()


def _ridge(x, y):
    penalty = np.eye(x.shape[1]) * L2
    penalty[0, 0] = 0.
    return np.linalg.solve(x.T @ x / len(y) + penalty,
                           x.T @ np.log1p(y) / len(y)).tolist()


class ContextTransitionModel:
    def __init__(self, metadata):
        if metadata.get("schema") != SCHEMA or metadata.get("source") != "live":
            raise ValueError("expected native context_transition_model_v1")
        if metadata.get("feature_names") != list(FEATURE_NAMES) or metadata.get("operations") != list(OPERATIONS):
            raise ValueError("frozen feature/operation schema differs")
        if metadata.get("seeds") != list(SEEDS) or metadata.get("aggregation") != "arithmetic_mean":
            raise ValueError("frozen seed aggregation differs")
        normalizer = metadata.get("normalizer", {})
        for key in ("mean", "scale"):
            values = normalizer.get(key, [])
            if len(values) != len(FEATURE_NAMES) or not all(math.isfinite(v) for v in values):
                raise ValueError("invalid training normalizer")
        if any(value < 1. for value in normalizer["scale"]):
            raise ValueError("invalid training scale")
        for name, blind in (("context", False), ("coarse", True)):
            width = 1 + len(OPERATIONS) + (0 if blind else 2 * len(FEATURE_NAMES) * (1 + len(OPERATIONS)))
            for target in (*EFFECTS, "execution_seconds"):
                head = metadata["heads"][name][target]
                if head is None:
                    continue
                arrays = [head] if target == "execution_seconds" else head
                if target != "execution_seconds" and len(arrays) != len(SEEDS):
                    raise ValueError("missing fixed seed member")
                if any(len(w) != width or not all(math.isfinite(v) for v in w) for w in arrays):
                    raise ValueError("invalid model weight shape or value")
        self.metadata = metadata

    @classmethod
    def load(cls, path):
        return cls(json.loads(Path(path).read_text()))

    def predict_features(self, features, operation, blind=False):
        started = time.perf_counter()
        values, observed = feature_vector(features)
        unknown = {"status": "unknown", "success": None, "safety": None,
                   "execution_seconds": None, "utility": -1., "support": 0,
                   "known_support": {key: 0 for key in (*EFFECTS, "execution_seconds")},
                   "missing_features": [n for n, flag in zip(FEATURE_NAMES, observed) if not flag]}
        reason = None
        if operation not in OPERATIONS:
            reason = "unsupported_operation"
        elif not observed.any():
            reason = "all_features_unknown"
        cell = self.metadata["support"].get(operation, {})
        support = cell
        if not blind and reason is None:
            required = _required_configuration(features)
            if any(not observed[FEATURE_NAMES.index(name)] for name in required):
                reason = "required_configuration_features_unknown"
            elif any(not cell.get("observed_features", {}).get(name, 0) for name in required):
                reason = "required_configuration_features_unseen_in_training"
            elif any(present and not cell.get("observed_features", {}).get(name, 0)
                     for name, present in zip(FEATURE_NAMES, observed)):
                reason = "observed_feature_unseen_for_operation_in_training"
        if reason is None and not support.get("attempts", 0):
            reason = "unsupported_operation"
        if reason:
            return {**unknown, "reason": reason, "prediction_seconds": time.perf_counter()-started}
        x = _design(values, observed, operation, self.metadata["normalizer"], blind)
        heads = self.metadata["heads"]["coarse" if blind else "context"]
        result = {**unknown, "support": support["attempts"],
                  "known_support": {key: support.get(key, 0) for key in (*EFFECTS, "execution_seconds")}}
        for target in EFFECTS:
            if heads[target] is not None and support.get(target, 0):
                result[target] = float(np.mean([_sigmoid(x @ np.asarray(w)) for w in heads[target]]))
        if heads["execution_seconds"] is not None and support.get("execution_seconds", 0):
            result["execution_seconds"] = float(np.expm1(np.clip(x @ np.asarray(heads["execution_seconds"]), 0., 20.)))
        if all(result[target] is not None for target in (*EFFECTS, "execution_seconds")):
            result["status"] = "ok"
            result["utility"] = result["success"] - (1-result["safety"]) - .002*result["execution_seconds"] - (.0 if operation == "no_action" else .01)
        else:
            result["reason"] = "missing_native_label_support"
        result["prediction_seconds"] = time.perf_counter()-started
        return result


def fit_context_model(rows, *, provenance=None):
    """Fit only supplied, independently validated train observations."""
    started = time.perf_counter()
    if not rows or any(row.get("source") != "live" or not row.get("labels_verified") for row in rows):
        raise ValueError("fit requires independently verified native rows")
    if any(row.get("stage") not in ("train", "training") for row in rows):
        raise ValueError("only train rows may enter fitting")
    prepared = []
    for row in rows:
        values, observed = feature_vector(row["features"])
        if observed.any() and row["operation"] in OPERATIONS:
            prepared.append((row, values, observed))
    if not prepared:
        raise ValueError("no native training observations with context features")
    values = np.asarray([entry[1] for entry in prepared])
    masks = np.asarray([entry[2] for entry in prepared])
    counts = masks.sum(axis=0)
    mean = (values*masks).sum(axis=0) / np.maximum(counts, 1)
    scale = np.sqrt((((values-mean)*masks)**2).sum(axis=0) / np.maximum(counts, 1))
    normalizer = {"mean": mean.tolist(), "scale": np.maximum(scale, 1.).tolist(), "observed_count": counts.tolist()}
    support = {}
    for row, _, observed in prepared:
        cell = support.setdefault(row["operation"], {"attempts": 0, "masks": {}})
        mask_cell = cell["masks"].setdefault(_mask_key(observed), {"attempts": 0})
        feature_support = cell.setdefault("observed_features", {})
        for name, present in zip(FEATURE_NAMES, observed):
            feature_support[name] = feature_support.get(name, 0) + int(present)
        for bucket in (cell, mask_cell):
            bucket["attempts"] += 1
            for target in (*EFFECTS, "execution_seconds"):
                bucket[target] = bucket.get(target, 0) + int(row.get(target) is not None)
    heads = {}
    for name, blind in (("context", False), ("coarse", True)):
        x = np.asarray([_design(v, m, row["operation"], normalizer, blind) for row, v, m in prepared])
        heads[name] = {}
        for target in (*EFFECTS, "execution_seconds"):
            known = np.asarray([row.get(target) is not None for row, _, _ in prepared])
            y = np.asarray([row[target] for row, _, _ in prepared if row.get(target) is not None], dtype=float)
            if not len(y):
                heads[name][target] = None
            elif target == "execution_seconds":
                heads[name][target] = _ridge(x[known], y)
            else:
                heads[name][target] = [_logistic(x[known], y, seed) for seed in SEEDS]
    metadata = {"schema": SCHEMA, "source": "live", "feature_names": list(FEATURE_NAMES),
                "operations": list(OPERATIONS), "seeds": list(SEEDS), "aggregation": "arithmetic_mean",
                "normalizer": normalizer, "heads": heads, "support": support,
                "parameters": {"iterations": ITERATIONS, "learning_rate": LEARNING_RATE, "l2": L2},
                "support_rule": {"core_features": list(CORE_FEATURES), "core_requirements": CORE_SUPPORT_RULE, "optional_missing": "train mean plus explicit missing mask; no exact-mask matching", "newly_observed_feature": "unknown if feature was never observed for operation in training", "label_support": "same operation native known targets"},
                "training_attempt_ids": [r["id"] for r in rows], "training_total": len(rows),
                "training_feature_rows": len(prepared), "training_known": {target: sum(r.get(target) is not None for r in rows) for target in (*EFFECTS, "execution_seconds")},
                "training_fitted_known": {target: sum(r.get(target) is not None for r, _, _ in prepared) for target in (*EFFECTS, "execution_seconds")},
                "provenance": provenance or {}, "created_at": time.time(),
                "cost": {"fit_seconds": time.perf_counter()-started, "tokens": 0, "cpu_threads": 2},
                "calibration_status": "unrecalibrated_laboratory_point_estimates_not_admission_bounds"}
    return ContextTransitionModel(metadata)


def queue_manifest(queue, *, expected_stage=None):
    path = Path(queue).resolve()
    if path.is_dir():
        path = path / "PREREGISTRATION.json"
    plan = json.loads(path.read_text())
    stage = plan.get("stage") or plan.get("split")
    # Older stage-specific directories can be read, but mixed queues are rejected.
    if stage is None:
        stage = next((s for s in ("training", "validation", "test", "online")
                      if path.parent.name.startswith(s + "_")), None)
    stage = "train" if stage == "training" else stage
    expected_stage = "train" if expected_stage == "training" else expected_stage
    if stage not in ("train", "validation", "test", "online", "policy") or expected_stage and stage != expected_stage:
        raise ValueError("queue stage does not match permitted split: " + str(stage))
    for assignment in plan.get("assignments", []):
        assigned_stage = assignment.get("stage", stage)
        assigned_stage = "train" if assigned_stage == "training" else assigned_stage
        if assigned_stage != stage:
            raise ValueError("mixed split assignment in queue")
    return path, plan, stage


def _resolve_artifact(value, queue_path):
    path = Path(value)
    return path.resolve() if path.is_absolute() else (queue_path.parent/path).resolve()


def _execution_time(raw):
    execution = raw.get("execution", {})
    if execution.get("status") == "no_action":
        return 0.
    if execution.get("status") != "executed" and not (execution.get("status") == "controller_failed" and execution.get("all_writes_applied") is True):
        return None
    timings = execution.get("timings_seconds", {})
    terms = [v for k, v in timings.items() if k.startswith(("write_", "controller_"))]
    if not terms:
        return None
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in terms):
        raise ValueError("invalid measured transition duration")
    return float(sum(terms))


def load_queue(queue, *, expected_stage=None):
    """Read this queue's selected actions, retaining every registered position."""
    path, plan, stage = queue_manifest(queue, expected_stage=expected_stage)
    rows, artifacts = [], {str(path): file_digest(path)}
    seen, output_paths = set(), set()
    for assignment in plan["assignments"]:
        output = _resolve_artifact(assignment["out"], path)
        if output in output_paths:
            raise ValueError("duplicate campaign output in queue")
        output_paths.add(output)
        config_path = _resolve_artifact(assignment["config"], path)
        config = json.loads(config_path.read_text())
        artifacts[str(config_path)] = file_digest(config_path)
        ledger = output / "episodes.jsonl"
        if ledger.exists():
            raw_rows = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
            artifacts[str(ledger)] = file_digest(ledger)
        else:
            raw_rows = []
            for episode_path in sorted(output.glob("episode-*/episode.json")):
                raw_rows.append(json.loads(episode_path.read_text()))
                artifacts[str(episode_path)] = file_digest(episode_path)
        scheduled = config.get("scheduled_jobs")
        expected = len(scheduled) if scheduled is not None else len(config.get("cases", []))*config.get("repeats", 1)*len(config.get("arms", []))
        if len(raw_rows) > expected:
            raise ValueError("more attempts than registered positions")
        for raw in raw_rows:
            identity = str(output) + "/" + raw["episode_id"]
            if identity in seen:
                raise ValueError("duplicate native attempt")
            seen.add(identity)
            _, selected = validate_record(raw)
            if raw.get("source") != "live":
                raise ValueError("context fitting/evaluation requires native provenance")
            operation = (selected.actions[0].operation if len(selected.actions) == 1 else "no_action" if not selected.actions else "unsupported") if selected else "unknown"
            witness_path = output / raw["episode_id"] / "action_context_candidates.json"
            witness = {}
            if witness_path.exists():
                package = json.loads(witness_path.read_text())
                if package.get("schema") != "action_context_candidates_v1":
                    raise ValueError("unexpected candidate context schema")
                witness = package["candidates"].get(raw["chosen_plan"], {})
                artifacts[str(witness_path)] = file_digest(witness_path)
                captured_at = package.get("captured_at", package.get("at", witness.get("captured_at")))
                if captured_at is not None and captured_at > raw.get("action_started_at", float("inf")):
                    raise ValueError("post-action context cannot train a pre-action predictor")
                if witness.get("evaluated_at", 0) > raw.get("action_started_at", float("inf")):
                    raise ValueError("post-action projection cannot train a pre-action predictor")
            feature_vector(witness.get("features", {}))
            labels = raw.get("outcome", {}).get("effects", {})
            rows.append({"id": identity, "episode_id": raw["episode_id"], "source": "live", "labels_verified": True,
                         "stage": stage, "fault": assignment.get("fault", "unregistered"),
                         "state_condition": assignment.get("state_condition"), "operation": operation,
                         "chosen_plan": raw.get("chosen_plan"), "status": raw.get("status", "unknown"),
                         "features": witness.get("features", {}), "gate_status": witness.get("status", "unknown"),
                         "success": labels.get("business_recovered"), "safety": labels.get("non_target_safe"),
                         "execution_seconds": _execution_time(raw), "timings_seconds": raw.get("timings_seconds", {}),
                         "probe_budget": raw.get("total_probe_budget", {}),
                         "api_diagnostics": raw.get("execution", {}).get("goal_policy_diagnostics", {})})
        for index in range(len(raw_rows), expected):
            rows.append({"id": str(output)+"/not_started-"+str(index), "source": "live", "labels_verified": True,
                         "stage": stage, "fault": assignment.get("fault", "unregistered"),
                         "state_condition": assignment.get("state_condition"), "operation": "unknown", "chosen_plan": None,
                         "status": "not_started", "features": {}, "gate_status": "unknown",
                         "success": None, "safety": None, "execution_seconds": None, "timings_seconds": {}, "probe_budget": {}})
    declared = plan.get("planned_positions", plan.get("planned_attempts"))
    if declared is not None and len(rows) != declared:
        raise ValueError("queue position count differs from preregistration")
    if not rows:
        raise ValueError("empty registered queue")
    return rows, {"path": str(path), "stage": stage, "sha256": file_digest(path), "artifacts": artifacts}


def _binary_metrics(rows, forecasts, target):
    pairs = [(r[target], p[target]) for r, p in zip(rows, forecasts) if r.get(target) is not None and p.get(target) is not None]
    bins = []
    for i, (lo, hi) in enumerate(zip(CALIBRATION_EDGES[:-1], CALIBRATION_EDGES[1:])):
        members = [(y, p) for y, p in pairs if lo <= p < hi or i == len(CALIBRATION_EDGES)-2 and p == hi]
        bins.append({"lower": lo, "upper": hi, "count": len(members),
                     "mean_prediction": float(np.mean([p for y, p in members])) if members else None,
                     "observed_rate": float(np.mean([y for y, p in members])) if members else None})
    return {"total": len(rows), "known_labels": sum(r.get(target) is not None for r in rows),
            "missing_labels": sum(r.get(target) is None for r in rows), "scored": len(pairs),
            "known_but_prediction_unknown": sum(r.get(target) is not None and p.get(target) is None for r, p in zip(rows, forecasts)),
            "positives": sum(r.get(target) is True for r in rows),
            "brier": float(np.mean([(p-y)**2 for y, p in pairs])) if pairs else None,
            "ece": sum(b["count"]*abs(b["mean_prediction"]-b["observed_rate"]) for b in bins if b["count"])/len(pairs) if pairs else None,
            "calibration_bins": bins}


def evaluate_rows(model, rows):
    started = time.perf_counter()
    predictions = []
    for row in rows:
        predictions.append({"id": row["id"], "fault": row["fault"], "operation": row["operation"],
                            "gate_status": row["gate_status"], "labels": {key: row.get(key) for key in (*EFFECTS, "execution_seconds")},
                            "context": model.predict_features(row["features"], row["operation"]),
                            "coarse": model.predict_features(row["features"], row["operation"], blind=True)})
    def summarize(indices):
        subset = [rows[i] for i in indices]
        result = {"positions": len(subset), "attempt_status": dict(Counter(r["status"] for r in subset)),
                  "structural_gate": dict(Counter(r["gate_status"] for r in subset)),
                  "feature_known_missing": {name: {"known": sum(r["features"].get(name) is not None for r in subset),
                                                    "missing": sum(r["features"].get(name) is None for r in subset)}
                                             for name in FEATURE_NAMES}}
        for head in ("context", "coarse"):
            forecasts = [predictions[i][head] for i in indices]
            gated = [{**p, **({key: None for key in EFFECTS} if r["gate_status"] != "eligible" else {})} for r, p in zip(subset, forecasts)]
            time_pairs = [(r["execution_seconds"], p["execution_seconds"]) for r, p in zip(subset, forecasts)
                          if r.get("execution_seconds") is not None and p["execution_seconds"] is not None]
            result[head] = {"raw": {key: _binary_metrics(subset, forecasts, key) for key in EFFECTS},
                            "after_structural_gate": {key: _binary_metrics(subset, gated, key) for key in EFFECTS},
                            "supported": sum(p["status"] == "ok" for p in forecasts),
                            "unknown": sum(p["status"] == "unknown" for p in forecasts),
                            "gate_admitted": sum(r["gate_status"] == "eligible" and p["status"] == "ok" for r, p in zip(subset, forecasts)),
                            "execution_seconds_mae": float(np.mean([abs(p-y) for y, p in time_pairs])) if time_pairs else None,
                            "execution_seconds_scored": len(time_pairs)}
        result["cost"] = {"measured_execution_seconds": sum(r.get("execution_seconds") or 0 for r in subset),
                          "measured_attempt_wall_seconds": sum(r.get("timings_seconds", {}).get("total_attempt", 0) for r in subset),
                          "phase_seconds": {name: sum(r.get("timings_seconds", {}).get(name, 0) for r in subset)
                                            for name in sorted({k for r in subset for k in r.get("timings_seconds", {})})},
                          "prediction_seconds": sum(predictions[i][h]["prediction_seconds"] for i in indices for h in ("context", "coarse")),
                          "registered_probe_calls": sum(r.get("probe_budget", {}).get("calls", 0) for r in subset),
                          "registered_probe_seconds": sum(r.get("probe_budget", {}).get("probe_seconds", 0) for r in subset),
                          "probe_budget_known_attempts": sum("calls" in r.get("probe_budget", {}) for r in subset),
                          "tokens": 0}
        for key in ("api_calls", "api_seconds"):
            measured = [r.get("api_diagnostics", {}).get(key) for r in subset]
            known = [value for value in measured if value is not None]
            result["cost"][key] = sum(known) if known and len(known) == len(subset) else None
            result["cost"][key + "_observed_sum"] = sum(known) if known else None
            result["cost"][key + "_known_attempts"] = len(known)
            result["cost"][key + "_missing_attempts"] = len(subset) - len(known)
        api_known = sum(all(r.get("api_diagnostics", {}).get(key) is not None
                            for key in ("api_calls", "api_seconds")) for r in subset)
        result["cost"]["api_diagnostics_known_attempts"] = api_known
        result["cost"]["api_diagnostics_missing_attempts"] = len(subset) - api_known
        return result
    return {"schema": "context_transition_evaluation_v1", "overall": summarize(list(range(len(rows)))),
            "by_fault": {fault: summarize([i for i, r in enumerate(rows) if r["fault"] == fault]) for fault in sorted({r["fault"] for r in rows})},
            "predictions": predictions, "probe_budget_by_attempt": [{"id": r["id"], "probe_budget": r.get("probe_budget", {})} for r in rows],
            "evaluation_seconds": time.perf_counter()-started, "tokens": 0, "cpu_threads": 2,
            "interpretation": "Brier/calibration use measured labels only; gated metrics restrict coverage without converting blocked/unknown into negative labels."}
