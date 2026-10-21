#!/usr/bin/env python3
"""Fit the predeclared context and coarse models using only a training queue."""
import argparse
import json
import os
from pathlib import Path
import resource
import sys
import time

for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "2"
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from methods.E2R.intervention.context_transition import (
    CALIBRATION_EDGES, CORE_FEATURES, CORE_SUPPORT_RULE, FEATURE_NAMES, ITERATIONS, LEARNING_RATE, L2, OPERATIONS, SEEDS,
    evaluate_rows, file_digest, fit_context_model, load_queue, queue_manifest, save_json,
)


def _check_frozen_plan(path):
    plan = json.loads(Path(path).read_text())
    expected = {"schema": "context_transition_model_plan_v1", "fixed_before_native_training": True,
                "seeds": list(SEEDS), "iterations": ITERATIONS, "learning_rate": LEARNING_RATE,
                "l2": L2, "feature_names": list(FEATURE_NAMES), "operations": list(OPERATIONS),
                "core_configuration_features": list(CORE_FEATURES),
                "core_support_rule": CORE_SUPPORT_RULE,
                "calibration_bins": list(CALIBRATION_EDGES), "cpu_threads": 2, "tokens": 0,
                "deployment_aggregation": "arithmetic mean of all three fixed seeds"}
    for name, value in expected.items():
        if plan.get(name) != value:
            raise ValueError("implementation differs from frozen MODEL_PLAN: " + name)
    return plan


def run(train_queue, validation_queue, out):
    started = time.perf_counter()
    train_path, train_plan, _ = queue_manifest(train_queue, expected_stage="train")
    val_path, val_plan, _ = queue_manifest(validation_queue, expected_stage="validation")
    if train_path == val_path:
        raise ValueError("train and validation queues must be independent")
    def outputs(path, plan):
        return {(Path(a["out"]) if Path(a["out"]).is_absolute() else path.parent/ a["out"]).resolve() for a in plan["assignments"]}
    if outputs(train_path, train_plan) & outputs(val_path, val_plan):
        raise ValueError("train and validation source outputs overlap")
    model_plan_path = train_path.parent.parent / "MODEL_PLAN.json"
    if not model_plan_path.exists():
        raise ValueError("write study MODEL_PLAN.json before native training")
    frozen = _check_frozen_plan(model_plan_path)
    out = Path(out).resolve()
    if (out/"model.json").exists() or (out/"RESULTS.json").exists():
        raise ValueError("model/results already frozen; use a new output directory")
    out.mkdir(parents=True, exist_ok=True)
    save_json(out/"MODEL_PLAN.json", frozen)
    train, train_source = load_queue(train_path, expected_stage="train")
    model = fit_context_model(train, provenance={"train_queue": train_source,
                              "model_plan": str(model_plan_path), "model_plan_sha256": file_digest(model_plan_path),
                              "test_evaluated_during_fit": False})
    # Freeze the deployable model before touching validation observations.
    save_json(out/"model.json", model.metadata)
    validation, validation_source = load_queue(val_path, expected_stage="validation")
    if {r["id"] for r in train} & {r["id"] for r in validation}:
        raise ValueError("train and validation attempt identities overlap")
    result = {"schema": "context_transition_fit_results_v1", "status": "complete", "model_path": str(out/"model.json"),
              "model_sha256": file_digest(out/"model.json"), "model_plan_sha256": file_digest(model_plan_path),
              "train": evaluate_rows(model, train), "validation": evaluate_rows(model, validation),
              "validation_source": validation_source, "test_evaluated": False,
              "validation_used_for_fitting": False, "validation_recalibration": False,
              "selection": "fixed three-seed mean; no search or checkpoint selection",
              "cost": {**model.metadata["cost"], "total_seconds": time.perf_counter()-started,
                       "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024}}
    save_json(out/"RESULTS.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-queue", required=True)
    parser.add_argument("--validation-queue", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = run(args.train_queue, args.validation_queue, args.out)
    print(json.dumps({"model": result["model_path"], "test_evaluated": False, "cost": result["cost"]}))
