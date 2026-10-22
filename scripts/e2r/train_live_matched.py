#!/usr/bin/env python3
"""Train three matched E2R graph-only seeds on a frozen 60-case live split."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from methods.E2R.paths import default_dyrca_root
from methods.E2R.live_adapter import observation_from_snapshots
from scripts.e2r.run_graph_experiment import run


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_inputs(plan_path: Path, ledger_path: Path, manifest_path: Path,
                    split_path: Path, contexts_dir: Path) -> dict:
    plan = json.loads(plan_path.read_text())
    ledger = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    ledger_by_id = {row["case_id"]: row for row in ledger}
    manifest = json.loads(manifest_path.read_text())
    split = json.loads(split_path.read_text())
    cases = plan["cases"]
    if len(cases) != 60 or len(ledger) != 60 or any(row["status"] != "completed_clean" for row in ledger):
        raise ValueError("matched live training requires 60 completed_clean incidents")
    by_id = {row["case_id"]: row for row in cases}
    if len(by_id) != 60 or {row["case_id"] for row in ledger} != set(by_id):
        raise ValueError("plan/ledger case mismatch")
    expected = {"train": [], "val": [], "test": []}
    for case in cases:
        rep = case["repetition"]
        key = "train" if 1 <= rep <= 7 else "val" if rep == 8 else "test" if 9 <= rep <= 10 else None
        if key is None:
            raise ValueError("unsupported repetition number")
        expected[key].append(case["case_id"])
    if split != expected or {key: len(value) for key, value in split.items()} != {
            "train": 42, "val": 6, "test": 12}:
        raise ValueError("training split differs from predeclared 42/6/12 policy")
    observed = {row["case_id"]: row for row in manifest["cases"]}
    if len(observed) != 60 or set(observed) != set(by_id):
        raise ValueError("E2R observation conversion missing a case")
    for case_id, case in by_id.items():
        row = observed[case_id]
        if row["true_service"] != case["target"] or row["true_category"] != case["failure_type"]:
            raise ValueError(f"observation manifest label mismatch: {case_id}")
        snapshot = manifest_path.parent / row["observation_file"]
        if not snapshot.is_file():
            raise FileNotFoundError(snapshot)
        context_dir = contexts_dir / case_id
        for field, filename in (("pre", "pre.json"), ("during", "during.json"),
                                ("pre_topology", "pre_topology.json"),
                                ("during_topology", "during_topology.json")):
            if digest(context_dir / filename) != ledger_by_id[case_id]["context_files"][field]:
                raise ValueError(f"source context digest mismatch: {case_id}/{field}")
        if json.loads(snapshot.read_text()) != observation_from_snapshots(context_dir):
            raise ValueError(f"E2R observation differs from frozen context: {case_id}")
    return split


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--ledger", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--contexts-dir", type=Path, required=True)
    ap.add_argument("--split", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--dyrca-root", type=Path, default=default_dyrca_root())
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--threads", type=int, default=2)
    args = ap.parse_args()
    if args.out_dir.exists():
        raise FileExistsError(args.out_dir)
    split = validate_inputs(args.plan, args.ledger, args.manifest, args.split,
                            args.contexts_dir)
    args.out_dir.mkdir(parents=True)
    pre = {"status": "running", "source_sha256": {
        str(path.resolve()): digest(path) for path in
        (args.plan, args.ledger, args.manifest, args.split,
         Path(__file__), ROOT / "scripts/e2r/run_graph_experiment.py",
         ROOT / "methods/E2R/graph.py")},
        "training_variant": "patch", "seeds": [42, 43, 44],
        "observation_sha256": {row["case_id"]: digest(args.manifest.parent / row["observation_file"])
                               for row in json.loads(args.manifest.read_text())["cases"]},
        "device": args.device}
    (args.out_dir / "training_contract_pending.json").write_text(json.dumps(pre, indent=2) + "\n")
    run_args = argparse.Namespace(
        manifest=str(args.manifest.resolve()), out_dir=str(args.out_dir.resolve()),
        split_file=str(args.split.resolve()), data_root="", dyrca_root=str(args.dyrca_root.resolve()),
        planner="observation-rule", device=args.device,
        training_variant="patch", eval_variants=["patch"],
        limit=0, seed=42, seeds=[42, 43, 44], epochs=0, threads=args.threads)
    result = run(run_args)
    actual_split = json.loads((args.out_dir / "split.json").read_text())
    if actual_split != split or result.get("split_counts") != {key: len(value) for key, value in split.items()}:
        raise ValueError("trained split differs from frozen split")
    if result.get("seeds") != [42, 43, 44] or result.get("training_variant") != "patch" or \
            result.get("status") != "completed_offline_structural_ablation":
        raise ValueError("incomplete matched E2R graph training")
    import torch
    checkpoint_hashes = {}
    for seed in (42, 43, 44):
        checkpoint = args.out_dir / f"model_seed{seed}.pt"
        state = torch.load(checkpoint, map_location="cpu", weights_only=True)
        if state.get("seed") != seed or state.get("training_variant") != "patch":
            raise ValueError(f"checkpoint metadata mismatch: {seed}")
        checkpoint_hashes[checkpoint.name] = digest(checkpoint)
    contract = {"status": "complete_matched_live_training",
                "method_scope": "e2r_graph_only_not_full_e2r",
                "plan_sha256": digest(args.plan), "ledger_sha256": digest(args.ledger),
                "manifest_sha256": digest(args.manifest), "split_sha256": digest(args.split),
                "training_variant": "patch", "seeds": [42, 43, 44],
                "checkpoint_sha256": checkpoint_hashes,
                "observation_sha256": pre["observation_sha256"],
                "source_sha256": pre["source_sha256"],
                "training_device": result["runtime"]["device"]}
    (args.out_dir / "training_contract.json").write_text(json.dumps(contract, indent=2) + "\n")
    print("completed matched E2R graph-only training", args.out_dir, flush=True)


if __name__ == "__main__":
    main()
