#!/usr/bin/env python3
"""Evaluate a previously frozen context model; never fit or select on this queue."""
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
    ContextTransitionModel, evaluate_rows, file_digest, load_queue, queue_manifest, save_json,
)


def run(queue, model_path, out):
    started = time.perf_counter()
    model_path = Path(model_path).resolve()
    # The model is required and loaded before evaluation labels are opened.
    model = ContextTransitionModel.load(model_path)
    before = file_digest(model_path)
    queue_path, queue_plan, stage = queue_manifest(queue)
    train_source = model.metadata.get("provenance", {}).get("train_queue", {})
    if str(queue_path) == train_source.get("path") or stage == "train":
        raise ValueError("held-out evaluation cannot use the training queue")
    training_outputs = {str(Path(identity).parent) for identity in model.metadata["training_attempt_ids"]}
    outputs = {str((Path(a["out"]) if Path(a["out"]).is_absolute() else queue_path.parent/a["out"]).resolve())
               for a in queue_plan["assignments"]}
    if outputs & training_outputs:
        raise ValueError("evaluation campaign outputs overlap training")
    out = Path(out).resolve()
    if (out/"RESULTS.json").exists():
        raise ValueError("evaluation results already exist")
    if stage == "test":
        fit_results = model_path.parent/"RESULTS.json"
        if not fit_results.exists():
            raise ValueError("test requires completed frozen train/validation report")
        fitted = json.loads(fit_results.read_text())
        if fitted.get("model_sha256") != before or fitted.get("test_evaluated") is not False:
            raise ValueError("test model differs from frozen validation report")
    rows, source = load_queue(queue_path)
    if set(model.metadata["training_attempt_ids"]) & {r["id"] for r in rows}:
        raise ValueError("evaluation attempts overlap training")
    result = evaluate_rows(model, rows)
    result.update({"status": "complete", "stage": stage, "source": source, "model_path": str(model_path), "model_sha256": before,
                   "test_evaluated": stage == "test", "model_updated": False,
                   "total_seconds": time.perf_counter()-started,
                   "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024})
    if file_digest(model_path) != before:
        raise ValueError("frozen model changed during evaluation")
    save_json(out/"RESULTS.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = run(args.queue, args.model, args.out)
    print(json.dumps({"stage": result["stage"], "overall": result["overall"], "tokens": 0}))
