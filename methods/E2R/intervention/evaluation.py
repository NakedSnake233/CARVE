"""Frozen-input metrics and explicitly scoped matched-arm policy replay."""
from __future__ import annotations

import math
import copy
import random
import time
from collections import defaultdict
from dataclasses import asdict, replace

from .contracts import Policy, check_plan, feasible
from .data import group_key, validate_record, validate_split
from .schema import EFFECTS, input_bundle
from .runtime import rank_candidates
from .model import plan_signature
from .protocols import replay_key, anchor_key, attempt_denominators


class ManualEffects:
    """Local explicit-effect control; not a reproduction/result of GAVEL."""
    def predict(self, graph, plan):
        safe, eliminated = True, False
        routes = {r.source:r.target for r in graph.active_relations() if r.kind == "routes_to" and r.source in graph.goals}
        known_routes = {r.source for r in graph.relations if r.kind == "routes_to" and r.source in graph.goals
                        and r.observed_at <= graph.at <= r.observed_at+r.ttl}
        state = {e.id:{k:graph.read(e.id,k) for k in ("capacity","load","healthy","replicas","latency_ms")} for e in graph.entities}
        for action in plan.actions:
            if action.operation == "scale":
                current = state[action.target]["replicas"]
                if current and current > 0:
                    cap = state[action.target]["capacity"]
                    if cap is not None:
                        state[action.target]["capacity"] = cap*action.parameters["replicas"]/current
                    state[action.target]["replicas"] = action.parameters["replicas"]
                for relation in graph.active_relations():
                    if relation.source == action.target and relation.kind == "competes_for":
                        free = graph.read(relation.target, "connection_headroom")
                        each = graph.read(action.target, "connections_per_replica")
                        if None in (free, each, current):
                            safe = None
                        elif safe is not False:
                            safe = free >= (action.parameters["replicas"]-current)*each
            elif action.operation == "restore_selector":
                routes[action.target] = action.destination
                known_routes.add(action.target)
                eliminated |= graph.read(action.target,"selector_matches") is False
            else:
                reference = state[action.destination or action.target]
                state[action.target].update({k:reference[k] for k in ("capacity","load","healthy","latency_ms")})
                eliminated |= graph.read(action.target,"ready_replicas") == 0
        # Replacing a Deployment template does not repair an unmatched Service selector.
        def all_known(values):
            return False if False in values else None if None in values else all(values)
        healthy, capacities, latency_checks = [], [], []
        for goal in graph.goals:
            target = routes.get(goal)
            if target is None:
                healthy.append(False if goal in known_routes else None)
                capacities.append(False if goal in known_routes else None)
                continue
            target_state = state[target]
            healthy.append(target_state["healthy"])
            cap, load = target_state["capacity"], target_state["load"]
            capacities.append(None if cap is None or load is None else cap >= load)
            latency, ceiling = target_state['latency_ms'], graph.read(goal,'latency_budget_ms')
            if ceiling is not None:
                latency_checks.append(None if latency is None else latency<=ceiling)
        routed, capacity = all_known(healthy), all_known(capacities)
        recovered = None if None in (capacity, safe, routed) else capacity and safe and routed
        if latency_checks:
            recovered = all_known([recovered,*latency_checks])
        values = dict(zip(EFFECTS, (routed, capacity, recovered, safe, eliminated)))
        return {"probabilities": {k: None if v is None else float(v) for k, v in values.items()},
                "spread": {k: 0.0 for k in EFFECTS}, "support": {k: 0 for k in EFFECTS},
                "source": "manual_effect_control", "unseen_signature": False, "outside_training_range": [],
                "note": "handwritten effects; not trained or an upstream GAVEL reproduction"}


class FixedPlanControl(ManualEffects):
    """Constant-action control, still subject to common constraints and validation risk."""
    def __init__(self, plan_id):
        self.plan_id = plan_id

    def predict(self, graph, plan):
        selected = plan.id == self.plan_id
        return {"probabilities":{k:float(selected) if k!="non_target_safe" else 1.0 for k in EFFECTS},
                "spread":{k:0.0 for k in EFFECTS},"support":{k:0 for k in EFFECTS},
                "source":"manual_effect_control","unseen_signature":False,"outside_training_range":[],
                "note":"constant-action screening forecast, not observed effects; empirical validation gate still applied"}


def policy_for_record(row, policy):
    """Use each deployment's registered operation catalog with common risk limits."""
    if "operation_policy" not in row:
        return policy
    local = Policy.from_dict(row["operation_policy"])
    return replace(local, min_utility=policy.min_utility, min_support=policy.min_support,
                   min_success_probability=policy.min_success_probability,
                   max_harm_probability=policy.max_harm_probability, capacity_margin=policy.capacity_margin)


def replay_groups(rows, protocol_matching=True):
    grouped = defaultdict(list)
    for row in rows:
        grouped[replay_key(row, protocol_matching)].append(row)
    counts = defaultdict(int)
    for group, condition, protocol in grouped:
        counts[(group, condition)] += 1
    return grouped, counts


def observed_plan_signature(row):
    registered = row.get('source_provenance', {}).get('plan_signature')
    if registered is not None:
        return registered
    if 'input' in row:
        graph, plans = input_bundle(row['input'])
        return plan_signature(graph, next(p for p in plans if p.id == row['chosen_plan']))
    return None


def replay_outcomes(samples, selected, anchor):
    signature = None
    if selected and anchor:
        graph, plans = input_bundle(anchor['input'])
        signature = plan_signature(graph, next(p for p in plans if p.id == selected['plan_id']))
    named = [r for r in samples if selected and r['chosen_plan'] == selected['plan_id']]
    observed = [r for r in named if observed_plan_signature(r) == signature]
    ambiguous = [r for r in named if observed_plan_signature(r) is None]
    outcomes = {}
    for key in EFFECTS:
        known = [float(r['outcome']['effects'][key]) for r in observed
                 if r.get('outcome', {}).get('effects', {}).get(key) is not None]
        attempts = len(observed) + len(ambiguous)
        outcomes[key] = {'mean':sum(known)/len(known) if known else None,'successes':sum(known),
                         'known':len(known),'attempts':attempts,'unknown':attempts-len(known)}
    untreated = [r for r in samples if observed_plan_signature(r) == '[]']
    no_action_outcomes = {}
    for key in EFFECTS:
        known = [float(r['outcome']['effects'][key]) for r in untreated
                 if r.get('outcome', {}).get('effects', {}).get(key) is not None]
        no_action_outcomes[key] = {'mean':sum(known)/len(known) if known else None,'successes':sum(known),
                                  'known':len(known),'attempts':len(untreated),'unknown':len(untreated)-len(known)}
    evidence = {'selected_plan_signature':signature, 'matching_attempt_ids':[r['episode_id'] for r in observed],
                'unverified_named_arm_attempt_ids':[r['episode_id'] for r in ambiguous],
                'different_signature_named_attempt_ids':[r['episode_id'] for r in named if r not in observed+ambiguous],
                'anchor_attempt_id':anchor['episode_id'] if anchor else None,
                'anchor_original_slot':anchor.get('original_schedule_slot') if anchor else None,
                'anchor_rule':'first original slot with input, then pre-action time and stable attempt ID',
                'no_action_outcomes':no_action_outcomes,'no_action_attempt_ids':[r['episode_id'] for r in untreated]}
    return outcomes, bool(observed), evidence


def replay_conditions(rows, policy, predictor, *, protocol_matching=True,decision_function=None):
    grouped, counts = replay_groups(rows, protocol_matching)
    decisions = []
    for (group, condition, protocol), samples in sorted(grouped.items()):
        anchors = [r for r in samples if "input" in r]
        selected, plans, anchor, candidates = None, [], None, []
        decision_trace=None
        if anchors:
            anchor = min(anchors, key=anchor_key)
            graph, plans = input_bundle(anchor["input"])
            if decision_function is None:
                candidates = rank_candidates(graph, plans, policy_for_record(anchor, policy), predictor,
                    allow_synthetic=all(r["source"] == "synthetic" for r in samples),
                    allow_manual=isinstance(predictor, ManualEffects))
            else:
                decision_trace=decision_function(graph,plans,policy_for_record(anchor,policy),anchor)
                candidates=decision_trace['candidates']
            for candidate in candidates:
                candidate_plan = next(p for p in plans if p.id == candidate['plan_id'])
                candidate['plan_signature'] = plan_signature(graph, candidate_plan)
                candidate['action_count'] = len(candidate_plan.actions)
            selected = (next((r for r in candidates if r['plan_id']==decision_trace['selected_plan']),None)
                        if decision_function is not None else next((r for r in candidates if r["executable"]), None))
        outcomes, present, evidence = replay_outcomes(samples, selected, anchor)
        decisions.append({"group": group, "condition": condition, 'protocol_stratum':protocol,
                          'condition_weight':1/counts[(group,condition)],
                          "plan_id": selected["plan_id"] if selected else None,
                          "score": selected["utility"] if selected else None,
                          "has_actions": bool(next((p.actions for p in plans if selected and p.id == selected["plan_id"]), ())),
                          "attempted_episodes": len(samples),
                          "matching_arm_present": present, "outcomes": outcomes,
                          'candidates':candidates, 'decision_trace':decision_trace,
                          **attempt_denominators(samples), **evidence})
    return decisions


def summarize_decisions(decisions, threshold):
    selected = [r for r in decisions if r["score"] is not None and r["score"] >= threshold and r.get("has_actions", True)]
    successes, harms, unknown = [], [], 0
    lower_recovery = upper_recovery = lower_risk = upper_risk = 0.0
    for row in selected:
        weight = row.get('condition_weight', 1.0)
        business, safety = row["outcomes"]["business_recovered"], row["outcomes"]["non_target_safe"]
        if business["mean"] is not None:
            successes.append((weight, business["mean"]))
        if safety["mean"] is not None:
            harms.append((weight, 1-safety["mean"]))
        for observation, kind in ((business, "recovery"), (safety, "risk")):
            n = observation["attempts"]
            count = observation["successes"] if kind == "recovery" else observation["known"]-observation["successes"]
            lo, hi = (count/n, (count+observation["unknown"])/n) if n else (0.0, 1.0)
            if kind == "recovery":
                lower_recovery += weight*lo
                upper_recovery += weight*hi
            else:
                lower_risk += weight*lo
                upper_risk += weight*hi
        unknown += int(not row["matching_arm_present"] or business["unknown"] > 0 or safety["unknown"] > 0)
    n = sum(r.get('condition_weight',1.0) for r in decisions)
    covered = sum(r.get('condition_weight',1.0) for r in selected)
    selected_ids = {id(r) for r in selected}
    policy_lower = policy_upper = 0.0
    for row in decisions:
        outcome = (row['outcomes'] if id(row) in selected_ids else row.get('no_action_outcomes', {})).get('business_recovered', {})
        attempts = outcome.get('attempts', 0)
        lo, hi = ((outcome['successes']/attempts, (outcome['successes']+outcome['unknown'])/attempts)
                  if attempts else (0.,1.))
        weight = row.get('condition_weight',1.)
        policy_lower += weight*lo; policy_upper += weight*hi
    return {"conditions": n, "selected": covered, "coverage": covered/n if n else None,
            'protocol_cells':len(decisions), 'selected_cells':len(selected),
            'weighting':'equal original group-condition weight, shared equally by its protocol cells',
            "non_intervention_decisions": sum(r.get('condition_weight',1.0) for r in decisions
                if r.get("plan_id") is not None and not r.get("has_actions", True)),
            "recovery_among_measured": sum(w*v for w,v in successes)/sum(w for w,v in successes) if successes else None,
            "harm_among_measured": sum(w*v for w,v in harms)/sum(w for w,v in harms) if harms else None,
            "conditions_with_unknown_or_missing_arm": unknown,
            'unknown_count_unit':'protocol_cells', 'protocol_cells_with_unknown_or_missing_arm':unknown,
            'protocol_cells_without_anchor':sum(r.get('anchor_attempt_id') is None for r in decisions),
            "population_recovery_lower": lower_recovery/n if n else None,
            "population_recovery_upper": upper_recovery/n if n else None,
            'population_recovery_scope':'recovery credited to automatic interventions; excludes untreated recovery',
            'policy_recovery_lower':policy_lower/n if n else None,
            'policy_recovery_upper':policy_upper/n if n else None,
            'policy_recovery_scope':'matched-arm replay; abstention/no_action uses same-protocol untreated arm or unknown bounds',
            "risk_lower": lower_risk/covered if covered else None,
            "risk_upper": upper_risk/covered if covered else None}


def calibrate_policy(rows, split, policy, predictor, *, target_coverage=1.0, protocol_matching=True):
    """Freeze a common live/replay gate using only validation outcome denominators."""
    validate_split(rows, split)
    validation = [r for r in rows if group_key(r) in split["validation"]]
    decisions = replay_conditions(validation, replace(policy, min_utility=-1e9), predictor, protocol_matching=protocol_matching)
    thresholds = sorted({r["score"] for r in decisions if r["score"] is not None} | {1e9})
    options = []
    for threshold in thresholds:
        summary = summarize_decisions(decisions, threshold)
        risk = summary["risk_upper"] if summary["risk_upper"] is not None else 0.0
        if risk <= policy.max_harm_probability and (summary["coverage"] or 0) <= target_coverage:
            options.append((summary["coverage"] or 0, threshold, summary))
    selected = max(options, key=lambda x: (x[0], x[1])) if options else (0, 1e9, summarize_decisions(decisions, 1e9))
    metadata = getattr(predictor, "metadata", {})
    identity = copy.deepcopy(metadata)
    return {"schema": "e2r_intervention_validation_profile_v1", "status": "selected" if selected[0] > 0 else "abstain",
            "policy": asdict(replace(policy, min_utility=selected[1])),
            "threshold_source": "validation_only", "validation_groups": list(split["validation"]),
            "target_coverage": target_coverage, "validation_metrics": selected[2],
            'protocol_matching':protocol_matching,
            "risk_limit": policy.max_harm_probability, "risk_semantics": "empirical unknown-inclusive bound, not a population safety guarantee",
            "predictor": "manual_effect_control" if isinstance(predictor, ManualEffects) else "learned",
            "training_configuration": identity}


def grouped_bootstrap(decisions, threshold, seed=42, repetitions=300):
    by_group = defaultdict(list)
    for row in decisions:
        by_group[row["group"]].append(row)
    if len(by_group) < 2:
        return {"status": "insufficient_independent_groups", "groups": len(by_group)}
    keys, rng, values = sorted(by_group), random.Random(seed), []
    for _ in range(repetitions):
        sample = [r for _ in keys for r in by_group[rng.choice(keys)]]
        values.append(summarize_decisions(sample, threshold)["population_recovery_lower"])
    values.sort()
    return {"status": "descriptive_group_bootstrap", "groups": len(keys), "repetitions": repetitions,
            "population_recovery_lower_ci95": [values[int(0.025*repetitions)], values[min(repetitions-1, int(0.975*repetitions))]]}


def evaluate(rows, split, policy, predictor, *, partition="test", coverages=(0.25, 0.5, 0.75, 1.0), protocol_matching=True):
    validate_split(rows, split)
    chosen = [r for r in rows if group_key(r) in split[partition]]
    validation = [r for r in rows if group_key(r) in split["validation"]]
    errors, times, sizes = defaultdict(list), [], []
    for row in chosen:
        graph, plan = validate_record(row, allow_synthetic=True)
        if graph is None:
            continue
        started = time.perf_counter()
        forecast = predictor.predict(graph, plan)
        times.append(time.perf_counter()-started)
        sizes.extend(forecast.get("graph_sizes", []))
        for key in EFFECTS:
            target, probability = row["outcome"]["effects"][key], forecast["probabilities"][key]
            if target is not None and probability is not None:
                errors[key].append((float(target)-probability)**2)
    screening = replace(policy, min_utility=-1e9)
    valid_decisions = replay_conditions(validation, screening, predictor, protocol_matching=protocol_matching)
    test_decisions = replay_conditions(chosen, screening, predictor, protocol_matching=protocol_matching)
    val_scores = sorted((r["score"] for r in valid_decisions if r["score"] is not None), reverse=True)
    curve = []
    for coverage in coverages:
        profile = calibrate_policy(rows, split, policy, predictor, target_coverage=coverage, protocol_matching=protocol_matching)
        threshold = profile["policy"]["min_utility"]
        point = {"requested_validation_coverage": coverage, "threshold": threshold,
                 "threshold_source": "validation_only", "actual_validation": summarize_decisions(valid_decisions, threshold),
                 **summarize_decisions(test_decisions, threshold)}
        point["frozen_policy_profile"] = profile
        point["bootstrap"] = grouped_bootstrap(test_decisions, threshold)
        curve.append(point)
    return {"schema": "e2r_intervention_evaluation_v1", "source": sorted({r["source"] for r in rows}),
            "claim_status": "engineering_only" if any(r["source"] == "synthetic" for r in rows) else "requires_claim_audit",
            "partition": partition, "episodes": len(chosen), "groups": len(split[partition]),
            'protocol_matching':protocol_matching, 'attempt_denominators':attempt_denominators(chosen),
            "attempts_without_predictor_input": sum("input" not in r for r in chosen),
            "coverage_scope": "attempted conditions; no-action is not automatic intervention coverage",
            "effect_brier": {k: {"mean": sum(errors[k])/len(errors[k]) if errors[k] else None,
                                  "known_predictions": len(errors[k]), "unmeasured": len(chosen)-len(errors[k])} for k in EFFECTS},
            "mean_inference_seconds": sum(times)/len(times) if times else None,
            "mean_retained_nodes": sum(x["retained"] for x in sizes)/len(sizes) if sizes else None,
            "risk_coverage": curve, "decisions": test_decisions,
            "policy_value_scope": "matched-condition arm replay with fixed pre-action anchor; not a live rollout or identified individual counterfactual",
            "limitations": ["Missing alternative arms remain unknown.", "Empirical risk is not a safety guarantee.",
                            "This frozen-input evaluation does not test active observation/refinement."]}
