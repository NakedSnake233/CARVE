#!/usr/bin/env python3
"""Convert completed paired live snapshots into E2R graph observations."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from methods.E2R.live_adapter import observation_from_snapshots


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--ledger", type=Path, required=True)
    ap.add_argument("--contexts-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--allow-partial-smoke", action="store_true")
    args = ap.parse_args()
    if args.out_dir.exists() and any(args.out_dir.iterdir()):
        raise FileExistsError("refusing to overwrite E2R observation run")
    plan = json.loads(args.plan.read_text())
    ledger = [json.loads(line) for line in args.ledger.read_text().splitlines()]
    terminal = {row["case_id"]: row for row in ledger}
    if len(terminal) != len(ledger):
        raise ValueError("duplicate terminal case")
    cases = plan["cases"]
    if args.allow_partial_smoke:
        cases = [row for row in cases if terminal.get(row["case_id"], {}).get("status") == "completed_clean"]
    elif len(terminal) != len(cases) or any(terminal.get(row["case_id"], {}).get("status") != "completed_clean"
                                             for row in cases):
        raise ValueError("all planned cases must be completed_clean for formal conversion")
    args.out_dir.mkdir(parents=True)
    observations = args.out_dir / "observations"
    observations.mkdir()
    manifest_rows = []
    for case in cases:
        case_id = case["case_id"]
        source = args.contexts_dir / case_id
        expected = terminal[case_id].get("context_files") or {}
        for key, filename in (("pre", "pre.json"), ("during", "during.json"),
                              ("pre_topology", "pre_topology.json"),
                              ("during_topology", "during_topology.json")):
            if hashlib.sha256((source / filename).read_bytes()).hexdigest() != expected.get(key):
                raise ValueError(f"source snapshot hash mismatch: {case_id}/{key}")
        observation = observation_from_snapshots(source)
        if case["target"] not in observation["candidates"]:
            raise ValueError(f"hidden target absent from candidates: {case_id}")
        relative = Path("observations") / f"{case_id}.json"
        (args.out_dir / relative).write_text(json.dumps(observation, indent=2) + "\n")
        manifest_rows.append({"case_id": case_id, "app": case["env"],
                              "scenario": case["failure_type"],
                              "true_service": case["target"],
                              "true_category": case["failure_type"],
                              "observation_file": str(relative),
                              "candidate_count": len(observation["candidates"]),
                              "coverage": observation["coverage"]})
    manifest = {"schema": "e2r_live_observation_v1",
                "status": ("partial_smoke_not_paper_result" if args.allow_partial_smoke
                           else "completed_live_observation_conversion"),
                "source_plan": str(args.plan.resolve()),
                "source_ledger": str(args.ledger.resolve()),
                "input_policy": "pre/during hidden-oracle snapshots; no post state or injected selector",
                "cases": manifest_rows}
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("converted", len(manifest_rows), "cases to", args.out_dir)


if __name__ == "__main__":
    main()
