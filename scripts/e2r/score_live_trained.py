#!/usr/bin/env python3
"""Score trained E2R graph checkpoints end to end on frozen live test IDs.

The timer starts before reading the saved pre/during bundle and ends after CPU
ranking. This is comparable to the adapted Eadro timed replay on the same CPU
host; GPU forward-only training metrics remain separate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from methods.E2R.live_adapter import E2RGraphLiveRCA


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--contexts-dir", type=Path, required=True)
    ap.add_argument("--split", type=Path, required=True)
    ap.add_argument("--checkpoints", type=Path, required=True)
    ap.add_argument("--training-manifest", type=Path)
    ap.add_argument("--hash-manifest", type=Path, required=True)
    ap.add_argument("--transfer-smoke", action="store_true")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seeds", type=int, nargs="+", default=[42, 43, 44])
    args = ap.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    split = json.loads(args.split.read_text())
    cases = split["test"]
    expected_hashes = json.loads(args.hash_manifest.read_text())
    if expected_hashes.get("schema") != "label_free_context_hashes_v1":
        raise ValueError("unsupported context hash manifest")
    if not set(cases).issubset(expected_hashes["cases"]):
        raise ValueError("test case missing frozen context digest")
    if not args.transfer_smoke:
        if args.seeds != [42, 43, 44] or args.training_manifest is None:
            raise ValueError("matched scoring requires three seeds and training manifest")
        results = json.loads((args.checkpoints / "results.json").read_text())
        trained_split = json.loads((args.checkpoints / "split.json").read_text())
        if trained_split != split:
            raise ValueError("checkpoint run is not bound to the declared split")
        if (results.get("training_variant") != "patch" or
                results.get("seeds") != args.seeds or
                results.get("split_counts") != {key: len(value) for key, value in split.items()} or
                results.get("status") != "completed_offline_structural_ablation"):
            raise ValueError("checkpoint run is not a complete matched patch-training run")
        contract = json.loads((args.checkpoints / "training_contract.json").read_text())
        trained_source = results.get("source_manifest")
        if (not isinstance(trained_source, str) or
                contract.get("source_sha256", {}).get(trained_source) !=
                hashlib.sha256(args.training_manifest.read_bytes()).hexdigest()):
            raise ValueError("checkpoint run is not bound to the declared training manifest")
        if (contract.get("status") != "complete_matched_live_training" or
                contract.get("manifest_sha256") != hashlib.sha256(args.training_manifest.read_bytes()).hexdigest() or
                contract.get("split_sha256") != hashlib.sha256(args.split.read_bytes()).hexdigest() or
                contract.get("ledger_sha256") != expected_hashes.get("source_ledger_sha256") or
                contract.get("seeds") != args.seeds or
                contract.get("training_variant") != "patch"):
            raise ValueError("matched live training contract mismatch")
        if set(expected_hashes["cases"]) != {case_id for ids in split.values() for case_id in ids}:
            raise ValueError("label-free context hash coverage differs from matched split")
        training_rows = json.loads(args.training_manifest.read_text())["cases"]
        if set(contract.get("observation_sha256") or {}) != {row["case_id"] for row in training_rows}:
            raise ValueError("training observation hash coverage mismatch")
        for row in training_rows:
            source = args.training_manifest.parent / row["observation_file"]
            if hashlib.sha256(source.read_bytes()).hexdigest() != contract["observation_sha256"][row["case_id"]]:
                raise ValueError(f"training observation changed: {row['case_id']}")
    import torch
    checkpoint_hashes = {}
    for seed in args.seeds:
        checkpoint = args.checkpoints / f"model_seed{seed}.pt"
        state = torch.load(checkpoint, map_location="cpu", weights_only=True)
        if (state.get("seed") != seed or state.get("training_variant") != "patch" or
                state.get("feature_policy") != "shared_observation_v1"):
            raise ValueError(f"checkpoint metadata mismatch for seed {seed}")
        checkpoint_hashes[seed] = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        if not args.transfer_smoke and contract["checkpoint_sha256"].get(f"model_seed{seed}.pt") != checkpoint_hashes[seed]:
            raise ValueError(f"checkpoint hash differs from completed contract: seed {seed}")
    methods = {seed: E2RGraphLiveRCA(seed, args.checkpoints / f"model_seed{seed}.pt")
               for seed in args.seeds}
    rows = []
    for case_id in cases:
            directory = args.contexts_dir / case_id
            hashes = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                      for name in ("pre.json", "during.json", "pre_topology.json",
                                   "during_topology.json")}
            declared = expected_hashes["cases"][case_id]
            for field, filename in (("pre", "pre.json"), ("during", "during.json"),
                                    ("pre_topology", "pre_topology.json"),
                                    ("during_topology", "during_topology.json")):
                if hashes[filename] != declared[field]:
                    raise ValueError(f"frozen input digest mismatch: {case_id}/{field}")
            for seed, method in methods.items():
                started = time.perf_counter()
                answer = method.diagnose_bundle(directory).normalized()
                seconds = time.perf_counter() - started
                name = (f"e2r_graph_transfer_s{seed}" if args.transfer_smoke
                        else f"e2r_graph_matched_s{seed}")
                row = {"case_id": case_id, "method": name,
                       "seed": seed, "checkpoint": str(method.checkpoint),
                       "checkpoint_sha256": checkpoint_hashes[seed],
                       "context_sha256": hashes["during.json"],
                       "bundle_sha256": {key: value for key, value in hashes.items()
                                         if key != "during.json"},
                       "status": "valid" if answer.ranked_services() else "abstain",
                       "ranking": answer.ranked_services(),
                       "predicted_category": None,
                       "diagnostic_seconds": seconds,
                       "provider_request_seconds": [],
                       "usage": {"input_tokens": 0, "output_tokens": 0,
                                 "total_tokens": 0, "cache_read_tokens": 0,
                                 "cache_creation_tokens": 0, "token_source": "zero"}}
                rows.append(row)
                print(case_id, seed, round(seconds, 4), flush=True)
    if len(rows) != len(cases) * len(args.seeds) or len({(r["case_id"], r["seed"]) for r in rows}) != len(rows):
        raise ValueError("matched test prediction row coverage failed")
    encoded = "".join(json.dumps(row) + "\n" for row in rows).encode()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temp = args.out.with_suffix(args.out.suffix + ".tmp")
    receipt = args.out.with_suffix(args.out.suffix + ".receipt.json")
    if temp.exists() or receipt.exists():
        raise FileExistsError("incomplete prior scoring artifact requires audit")
    with temp.open("wb") as output:
        output.write(encoded)
        output.flush()
        os.fsync(output.fileno())
    temp.replace(args.out)
    receipt.write_text(json.dumps({
        "status": "transfer_smoke_not_paper_result" if args.transfer_smoke else "complete_matched_test_scoring",
        "prediction_rows": len(rows), "test_cases": len(cases), "seeds": args.seeds,
        "predictions_sha256": hashlib.sha256(encoded).hexdigest(),
        "split_sha256": hashlib.sha256(args.split.read_bytes()).hexdigest(),
        "context_hash_manifest_sha256": hashlib.sha256(args.hash_manifest.read_bytes()).hexdigest(),
        "checkpoint_sha256": {f"model_seed{seed}.pt": digest
                              for seed, digest in checkpoint_hashes.items()},
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
