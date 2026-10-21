"""The live decision-to-verification spine; explicit execution is required."""
from __future__ import annotations

import json
import time
from pathlib import Path

from rca_bench.recovery_oracle import WorkloadWindow
from .campaign import KubernetesBackend
from .contracts import Policy
from .predictors import load_effect_predictor
from .platform import Executor, KubectlClient, LiveProvider
from .runtime import (ObservationBudget, execution_deviations, refine_and_decide, rank_candidates,
                      validated_policy, apply_profile_status)
from .schema import EvidenceGraph, Plan, GraphObservation, merge_observation


def run_live(config, model_directory, out_dir, *, execute=False, strategy="decision"):
    path = Path(out_dir)
    path.mkdir(parents=True, exist_ok=False)
    policy = Policy.from_dict(config["policy"])
    client = KubectlClient(policy.namespace, config.get("kubeconfig"))
    provider = LiveProvider(config["inventory"], client)
    model = load_effect_predictor(model_directory,seeds=config.get('prediction_seeds'),
                                  admission_path=config.get('admission_ledger'))
    profile = config.get("validation_profile")
    if isinstance(profile, str):
        profile = json.loads(Path(profile).read_text())
    policy, profile_reason = validated_policy(policy, model, profile)
    if profile_reason:
        decision={'schema':'e2r_intervention_decision_v1','decision':'abstain','selected_plan':None,
                  'candidates':[],'observations':[],'validation_profile_status':profile_reason,
                  'observed_verification':{'status':'not_executed'}}
        (path/'decision.json').write_text(json.dumps(decision,indent=2)+'\n')
        return {'decision':decision,'executed':False}
    started = time.perf_counter()
    graph = provider.collect()
    initial_observations = []
    requested = set(config.get("initial_probes", []))
    if not requested <= {p.id for p in provider.probes}:
        raise ValueError("initial observation includes an unknown or post-action probe")
    for probe in provider.probes:
        if probe.id in requested:
            began = time.perf_counter()
            fact = provider.probe(probe, graph)
            if isinstance(fact, GraphObservation):
                graph = merge_observation(graph, fact, time.time())
            else:
                graph.facts.append(fact)
                graph.at = time.time()
            initial_observations.append({"id": probe.id, "cost": probe.cost, "seconds": time.perf_counter()-began})
    graph.__post_init__()
    collection_seconds = time.perf_counter()-started
    plans = [Plan.from_dict(p) for p in config["plans"]]
    decision = refine_and_decide(graph, plans, policy, model, provider.probes, provider.probe,
                  budget=ObservationBudget(**config.get("observation_budget", {})), strategy=strategy,
                  fixed_order=config.get("fixed_probe_order"))
    apply_profile_status(decision, profile_reason)
    decision["initial_collection_seconds"] = collection_seconds
    decision["initial_observations"] = initial_observations
    (path / "decision.json").write_text(json.dumps(decision, indent=2, allow_nan=False) + "\n")
    if not execute or decision["decision"] != "execute":
        return {"decision": decision, "executed": False}
    # Baselines must be pre-incident measurements; never measure a faulty incident as its own baseline.
    if not config.get("baseline_windows"):
        raise ValueError("live execution requires independently saved pre-incident baseline_windows")
    backend = KubernetesBackend(config, path)
    backend.current_dir = path
    backend.baselines = {key: WorkloadWindow(**value) for key, value in config["baseline_windows"].items()}
    if set(backend.baselines) != set(backend.workloads):
        raise ValueError("baseline workloads do not match independent verification workloads")
    if any(w.end_time is None or w.end_time > graph.at for w in backend.baselines.values()):
        raise ValueError("baseline cannot include future/after-decision observations")
    chosen = next(p for p in plans if p.id == decision["selected_plan"])
    executor = Executor(client, provider, policy, path/"execution_private.jsonl",
                        convergence_timeout=config.get("convergence_timeout", 60))
    package = {"decision": decision, "executed": False, "execution": None, "verification": None}
    try:
        begun = time.time()
        def authorize(updated, remaining):
            assessment = rank_candidates(updated, [remaining], policy, model)[0]
            return {"approved": assessment["executable"], "assessment": assessment}
        execution = executor.execute(EvidenceGraph.from_dict(decision["graph"]), chosen,
                                     dry_run=False, authorize=authorize)
        package["execution"] = execution
        package["executed"] = execution["status"] == "executed"
        package["all_writes_applied"] = execution.get("all_writes_applied",False)
        package["action_started_at"] = begun
        if execution["status"] in ("executed", "no_action") or (execution["status"]=="controller_failed" and execution.get("all_writes_applied") is True):
            outcome = backend.verify(graph, chosen, execution, time.time())
            package["verification"] = outcome
            forecast = next(r["empirical_effects"] for r in decision["candidates"] if r["plan_id"] == chosen.id)
            package["deviations"] = execution_deviations(chosen.id, forecast, outcome["verification"])
            if outcome["verification"]["non_target_safety"] == "fail" or execution["status"]=="controller_failed":
                package["compensation"] = executor.compensate()
        else:
            package["compensation"] = executor.compensate()
    except Exception as exc:
        package["error"] = type(exc).__name__
        package["compensation"] = executor.compensate()
    finally:
        (path / "verification.json").write_text(json.dumps(package, indent=2, allow_nan=False) + "\n")
    return package
