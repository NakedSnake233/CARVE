#!/usr/bin/env python3
"""Exercise the actual CLI and all core training controls on synthetic data only."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    commands = []
    def run(*arguments):
        command = [sys.executable, "-m", "methods.E2R.intervention", *map(str, arguments)]
        started = time.perf_counter()
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        record = {"command": command, "exit_code": result.returncode, "seconds": time.perf_counter()-started}
        commands.append(record)
        if result.returncode:
            print(result.stderr[-3000:], file=sys.stderr)
            raise RuntimeError("smoke CLI stage failed")
        print(arguments[0], "passed", round(record["seconds"], 2), "s", flush=True)
    data = args.out / "data"
    run("example", "--out", data, "--groups", 8, "--repeats", 5)
    run("check", "--input", data/"input.json", "--policy", data/"policy.json", "--out", args.out/"check.json")
    configurations = [("main", "graph", "effect_distinguish"), ("no_edges", "set", "effect_distinguish"),
                      ("no_distinguish", "graph", "effect"), ("full_graph", "full", "effect_distinguish"),
                      ("diagnosis", "graph", "diagnosis"), ("state", "graph", "state")]
    for name, variant, objective in configurations:
        model = args.out / name
        run("train", "--data", data/"SYNTHETIC_episodes.jsonl", "--out", model,
            "--epochs", 4, "--hidden", 16, "--seeds", 42, "--variant", variant,
            "--objective", objective, "--allow-synthetic")
        run("evaluate", "--data", data/"SYNTHETIC_episodes.jsonl", "--model", model,
            "--policy", data/"policy.json", "--out", args.out/(name+"_evaluation.json"), "--allow-synthetic")
    run("calibrate", "--data", data/"SYNTHETIC_episodes.jsonl", "--model", args.out/"main",
        "--policy", data/"policy.json", "--out", args.out/"validation_profile.json", "--allow-synthetic")
    run("decide", "--input", data/"input.json", "--policy", data/"policy.json", "--model", args.out/"main",
        "--validation-profile", args.out/"validation_profile.json",
        "--out", args.out/"decision.json")
    decision = json.loads((args.out/"decision.json").read_text())
    if decision["decision"] != "abstain":
        raise RuntimeError("synthetic-trained model improperly authorized live execution")
    (args.out/"SMOKE_REPORT.json").write_text(json.dumps({"status": "passed", "source": "synthetic",
          "scope": "CLI/training/evaluation engineering only; not method superiority", "commands": commands,
          "synthetic_model_live_execution": "refused"}, indent=2)+"\n")


if __name__ == "__main__":
    main()
