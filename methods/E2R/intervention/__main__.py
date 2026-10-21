"""E2R intervention CLI: validate, train, decide, evaluate, observe and collect."""
import argparse
import json
from pathlib import Path

from .schema import input_bundle


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def check_package(bundle, policy=None):
    graph, plans = input_bundle(bundle)
    package = {"schema": "e2r_intervention_check_v1", "input_valid": True,
               "decision": "abstain", "reason": "no fitted effect model or execution policy",
               "deterministic_constraints": {"status": "not_checked"},
               "empirical_effects": {"status": "not_fitted", "predictions": None},
               "observed_verification": {"status": "not_executed"},
               "candidate_plans": [p.id for p in plans], "decision_at": graph.at}
    if policy is not None:
        from .contracts import check_plan, checks_dict
        package["deterministic_constraints"] = {p.id: checks_dict(check_plan(graph, p, policy)) for p in plans}
    return package


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "decide"):
        command = sub.add_parser(name)
        command.add_argument("--input", type=Path, required=True)
        command.add_argument("--policy", type=Path, required=name == "decide")
        command.add_argument("--out", type=Path, required=True)
        if name == "decide":
            command.add_argument("--model", type=Path, required=True)
            command.add_argument("--validation-profile", type=Path)
    example = sub.add_parser("example")
    example.add_argument("--out", type=Path, required=True)
    example.add_argument("--groups", type=int, default=8)
    example.add_argument("--repeats", type=int, default=5)
    train = sub.add_parser("train")
    train.add_argument("--data", type=Path, required=True)
    train.add_argument("--split", type=Path)
    train.add_argument("--out", type=Path, required=True)
    train.add_argument("--epochs", type=int, default=40)
    train.add_argument("--hidden", type=int, default=64)
    train.add_argument("--seeds", type=int, nargs="+", default=[42, 43, 44])
    train.add_argument("--variant", choices=["graph", "set", "full", "full_set"], default="graph")
    train.add_argument("--objective", choices=["effect_distinguish", "effect", "diagnosis", "state"], default="effect_distinguish")
    train.add_argument("--allow-synthetic", action="store_true")
    train.add_argument("--learning-rate", type=float, default=0.003)
    train.add_argument("--distinguish-weight", type=float, default=0.2)
    train.add_argument("--compact-weight", type=float, default=0.01)
    train.add_argument("--minimum-repeats", type=int, default=5)
    evaluation = sub.add_parser("evaluate")
    evaluation.add_argument("--data", type=Path, required=True)
    evaluation.add_argument("--model", type=Path, required=True)
    evaluation.add_argument("--policy", type=Path, required=True)
    evaluation.add_argument("--out", type=Path, required=True)
    evaluation.add_argument("--manual", action="store_true", help="local handwritten effect control, not GAVEL")
    evaluation.add_argument("--allow-synthetic", action="store_true")
    calibration = sub.add_parser("calibrate")
    for field in ("data", "model", "policy", "out"):
        calibration.add_argument("--"+field, type=Path, required=True)
    calibration.add_argument("--allow-synthetic", action="store_true")
    calibration.add_argument("--manual", action="store_true")
    live = sub.add_parser("run")
    live.add_argument("--config", type=Path, required=True)
    live.add_argument("--model", type=Path, required=True)
    live.add_argument("--out", type=Path, required=True)
    live.add_argument("--strategy", choices=["decision", "random", "fixed", "none"], default="decision")
    live.add_argument("--execute", action="store_true")
    for name in ("live-check", "collect"):
        command = sub.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        command.add_argument("--out", type=Path, required=True)
        if name == "collect":
            command.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    from .contracts import Policy
    if args.command == "check":
        policy = Policy.from_dict(json.loads(args.policy.read_text())) if args.policy else None
        write_json(args.out, check_package(json.loads(args.input.read_text()), policy))
    elif args.command == "example":
        from .examples import example_bundle, synthetic_records
        args.out.mkdir(parents=True, exist_ok=False)
        bundle, policy = example_bundle()
        write_json(args.out / "input.json", bundle)
        write_json(args.out / "policy.json", policy)
        rows = synthetic_records(groups=args.groups, repeats=args.repeats)
        with (args.out / "SYNTHETIC_episodes.jsonl").open("x") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")
        write_json(args.out / "PROVENANCE.json", {"source": "synthetic", "purpose": "engineering validation only",
                   "episodes": len(rows), "mechanism": "declared toy capacity/budget formula; no real execution"})
    elif args.command == "train":
        from .data import grouped_split, read_records
        from .learning import fit
        rows = read_records(args.data, allow_synthetic=args.allow_synthetic)
        split = json.loads(args.split.read_text()) if args.split else grouped_split(rows)
        fit(rows, split, args.out, seeds=args.seeds, epochs=args.epochs, hidden=args.hidden,
            variant=args.variant, objective=args.objective, allow_synthetic=args.allow_synthetic,
            learning_rate=args.learning_rate, distinguish_weight=args.distinguish_weight,
            compact_weight=args.compact_weight, minimum_repeats=args.minimum_repeats)
    elif args.command == "decide":
        from .learning import EffectPredictor
        from .runtime import refine_and_decide, ObservationBudget, validated_policy, apply_profile_status
        graph, plans = input_bundle(json.loads(args.input.read_text()))
        policy = Policy.from_dict(json.loads(args.policy.read_text()))
        predictor = EffectPredictor(args.model)
        profile = json.loads(args.validation_profile.read_text()) if args.validation_profile else None
        policy, reason = validated_policy(policy, predictor, profile)
        result = refine_and_decide(graph, plans, policy, predictor, [], None,
                                   budget=ObservationBudget(max_calls=0), strategy="none")
        apply_profile_status(result, reason)
        write_json(args.out, result)
    elif args.command in ("evaluate", "calibrate"):
        from .data import read_records
        from .learning import EffectPredictor
        from .evaluation import ManualEffects, evaluate, calibrate_policy
        rows = read_records(args.data, allow_synthetic=args.allow_synthetic)
        trained = EffectPredictor(args.model)
        predictor = ManualEffects() if args.manual else trained
        operation = evaluate if args.command == "evaluate" else calibrate_policy
        write_json(args.out, operation(rows, trained.metadata["split"],
                                     Policy.from_dict(json.loads(args.policy.read_text())), predictor))
    elif args.command == "run":
        from .service import run_live
        result = run_live(json.loads(args.config.read_text()), args.model, args.out,
                          execute=args.execute, strategy=args.strategy)
        print(json.dumps({"decision": result["decision"]["decision"], "executed": result["executed"]}))
    elif args.command == "live-check":
        from .platform import KubectlClient, LiveProvider
        config = json.loads(args.config.read_text())
        client = KubectlClient(config["inventory"]["namespace"], config.get("kubeconfig"))
        graph = LiveProvider(config["inventory"], client).collect()
        bundle = {"schema": "e2r_intervention_v1", "graph": graph.to_dict(), "plans": config["plans"]}
        args.out.mkdir(parents=True, exist_ok=False)
        write_json(args.out / "input.json", bundle)
        write_json(args.out / "check.json", check_package(bundle, Policy.from_dict(config["policy"])))
    else:
        from .campaign import run_campaign
        config = json.loads(args.config.read_text())
        if args.execute:
            result = run_campaign(config, args.out)
            print(json.dumps(result))
            return 0 if result["status"] == "complete" else 2
        write_json(args.out, {"status": "plan_only", "executed": False,
                             "planned": len(config["cases"])*len(config["arms"])*config.get("repeats", 5),
                             "namespace": config["experimental_namespace"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
