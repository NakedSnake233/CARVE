"""Bounded observation/refinement and candidate decisions under a shared policy."""
from __future__ import annotations

import copy
import random
import time
from dataclasses import asdict, dataclass, replace

from .contracts import Policy, check_plan, checks_dict, feasible
from .platform import Probe, graph_observation
from .schema import Fact, Plan, Witness, number, GraphObservation, merge_observation


@dataclass(frozen=True)
class ObservationBudget:
    max_calls: int = 3
    max_cost: float = 3.0
    max_seconds: float = 20.0

    def __post_init__(self):
        if self.max_calls < 0 or self.max_cost < 0 or self.max_seconds < 0:
            raise ValueError("observation budget cannot be negative")


def rank_candidates(graph, plans, policy, predictor, *, hypothetical=False, allow_synthetic=False, allow_manual=False):
    rows = []
    for plan in plans:
        checks = check_plan(graph, plan, policy, hypothetical=hypothetical)
        forecast = predictor.predict(graph, plan) if predictor else None
        reasons = []
        if not feasible(checks):
            reasons.append("operation_constraints")
        utility = -1.0
        if forecast is None:
            reasons.append("no_effect_model")
        else:
            p = forecast["probabilities"]
            success, safety = p.get("business_recovered"), p.get("non_target_safe")
            if number(success) and number(safety) and 0 <= success <= 1 and 0 <= safety <= 1:
                utility = success - (1-safety) - 0.01 * len(plan.actions)
                evidence = forecast.get('admission_evidence')
                if evidence is not None:
                    # v2 uses separately recorded validation evidence. These bounds
                    # cover missing outcomes, not sampling uncertainty or production risk.
                    if evidence.get('schema') != 'validation_empirical_admission_v2' or evidence.get('status') != 'supported':
                        reasons.append('missing_validation_admission_evidence')
                    else:
                        lower, upper = evidence.get('recovery_lower'), evidence.get('harm_upper')
                        if not number(lower) or not number(upper) or not 0 <= lower <= 1 or not 0 <= upper <= 1:
                            reasons.append('invalid_validation_admission_evidence')
                        else:
                            if lower < policy.min_success_probability:reasons.append('low_supported_success')
                            if upper > policy.max_harm_probability:reasons.append('high_predicted_risk')
                else:
                    # Preserve v1 semantics for archived models; no silent reinterpretation.
                    residuals = forecast.get("calibration", {})
                    success_error = max(2 * forecast["spread"].get("business_recovered", 0), residuals.get("business_recovered", 0))
                    safety_error = max(2 * forecast["spread"].get("non_target_safe", 0), residuals.get("non_target_safe", 0))
                    if not number(success_error) or not number(safety_error) or min(success_error, safety_error) < 0:
                        reasons.append("invalid_prediction_uncertainty")
                    if success-success_error < policy.min_success_probability:
                        reasons.append("low_supported_success")
                    if 1-safety+safety_error > policy.max_harm_probability:
                        reasons.append("high_predicted_risk")
            else:
                reasons.append("unobserved_or_invalid_outcome_dimension")
            manual = allow_manual and forecast.get("source") in ("manual_effect_control", "llm_baseline")
            if not manual and any(forecast["support"].get(k, 0) < policy.min_support for k in ("business_recovered", "non_target_safe")):
                reasons.append("insufficient_interventions")
            if forecast.get("unseen_signature") or forecast.get("outside_training_range"):
                reasons.append("outside_training_coverage")
            if forecast.get("source") != "live" and not allow_synthetic and not manual:
                reasons.append("no_live_training_support")
        if utility < policy.min_utility:
            reasons.append("validation_utility_threshold")
        rows.append({"plan_id": plan.id, "utility": utility, "executable": not reasons,
                     "reasons": reasons, "checks": checks_dict(checks), "empirical_effects": forecast})
    return sorted(rows, key=lambda r: (-r["utility"], r["plan_id"]))


def validated_policy(policy, predictor, profile, *, run_config=None, out_directory=None, runtime=None):
    if getattr(predictor,'metadata',{}).get('schema')=='e2r_intervention_training_v2':
        from .policy_qualification import validate_bounded_profile
        return validate_bounded_profile(policy,predictor,profile,run_config,out_directory,runtime)
    if not isinstance(profile, dict) or profile.get("schema") != "e2r_intervention_validation_profile_v1":
        return policy, "missing_validation_profile"
    if profile.get("status") != "selected" or profile.get("predictor") != "learned" or profile.get("threshold_source") != "validation_only":
        return policy, "validation_did_not_authorize_interventions"
    expected = predictor.metadata
    if profile.get("training_configuration") != expected:
        return policy, "validation_profile_model_mismatch"
    configured = asdict(policy)
    frozen = profile.get("policy", {})
    if any(frozen.get(k) != v for k, v in configured.items() if k != "min_utility"):
        return policy, "validation_profile_policy_mismatch"
    return Policy.from_dict(frozen), None


def apply_profile_status(decision, reason):
    decision["validation_profile_status"] = reason or "validated"
    if reason:
        decision["decision"], decision["selected_plan"] = "abstain", None
        for candidate in decision["candidates"]:
            candidate["executable"] = False
            candidate["reasons"].append(reason)
    return decision


def probe_adds_missing_evidence(graph,probe):
    variables={(probe.entity,probe.variable)}
    relations=set()
    if probe.kind in ("graph_json","native_graph"):
        variables|={(f['entity'],f['variable']) for value in probe.possible_values for f in value.get('facts',[])}
        relations|={(r['source'],r['target'],r['kind']) for value in probe.possible_values for r in value.get('relations',[])}
    ids={e.id for e in graph.entities}
    if any(e not in ids or graph.read(e,v) is None for e,v in variables):
        return True
    for key in relations:
        seen=[r for r in graph.relations if (r.source,r.target,r.kind)==key]
        if not seen:
            return True
        latest=max(seen,key=lambda r:r.observed_at)
        if latest.validity=='unknown' or not latest.observed_at<=graph.at<=latest.observed_at+latest.ttl:
            return True
    return False


def choose_probe(graph, plans, policy, predictor, probes, *, strategy="decision", seed=42, fixed_order=None, candidate_ranker=None):
    candidate_ranker = candidate_ranker or rank_candidates
    if strategy not in ("decision", "random", "fixed"):
        raise ValueError("unknown acquisition strategy")
    if not probes:
        return None, {}
    if strategy == "random":
        return random.Random(seed).choice(probes), {"method": "uniform_random"}
    if strategy == "fixed":
        priority={identity:i for i,identity in enumerate(fixed_order or [])}
        return sorted(probes, key=lambda p: (priority.get(p.id,len(priority)),p.id))[0], {"method": "fixed_order", "order":fixed_order}
    current = candidate_ranker(graph, plans, policy, predictor)
    missing = {tuple(k) for r in current for check in r["checks"] for k in check["missing_variables"]}

    def decision(rows):
        selected = next((r for r in rows if r["executable"]), None)
        return selected["plan_id"] if selected else None

    base = decision(current)
    values = []
    for probe in probes:
        probabilities = probe.probabilities or [1/len(probe.possible_values)] * len(probe.possible_values)
        changed, feasible_changes = 0.0, 0.0
        for value, probability in zip(probe.possible_values, probabilities):
            hypothetical = copy.deepcopy(graph)
            if probe.kind in ("graph_json", "native_graph"):
                hypothetical = merge_observation(hypothetical,
                    graph_observation(value, probe, hypothetical, graph.at, policy.namespace), graph.at)
            # Replace the current variable only in a private hypothetical copy.
            else:
                latest = hypothetical.latest(probe.entity, probe.variable)
                if latest:
                    hypothetical.facts = [f for f in hypothetical.facts if f.id != latest.id]
                hypothetical.facts.append(Fact(latest.id if latest else "hypothetical:" + probe.id, probe.entity, probe.variable, value,
                                           graph.at, probe.ttl, "probe_outcome_hypothesis", "hypothetical",
                                           graph.entity(probe.entity).uid))
            after = candidate_ranker(hypothetical, plans, policy, predictor, hypothetical=True)
            changed += probability * int(decision(after) != base)
            before_feasible = {r["plan_id"] for r in current if all(c["status"] in ("pass", "deferred") for c in r["checks"])}
            after_feasible = {r["plan_id"] for r in after if all(c["status"] in ("pass", "deferred") for c in r["checks"])}
            feasible_changes += probability * int(before_feasible != after_feasible)
        score = (changed + 0.25 * feasible_changes + 0.01 * int((probe.entity, probe.variable) in missing)) / probe.cost
        values.append((score, probe.id, probe, {"method": "finite_outcome_decision_change",
                      "decision_change_probability": changed, "feasibility_change_probability": feasible_changes,
                      "prior": "registered" if probe.probabilities else "uniform_heuristic", "score": score}))
    values.sort(key=lambda x: (-x[0], x[1]))
    return (values[0][2], values[0][3]) if values[0][0] > 0 else (None, {"reason": "no_predicted_decision_value"})


def refine_and_decide(graph, plans, policy: Policy, predictor, probes, observe, *,
                      budget=ObservationBudget(), strategy="decision", clock=time.monotonic, wall_clock=time.time, seed=42, fixed_order=None, candidate_ranker=None):
    candidate_ranker = candidate_ranker or rank_candidates
    started = clock()
    original_predictor = predictor
    prediction_work = {"calls": 0, "seconds": 0.0}
    class MeasuredPredictor:
        def predict(self, graph, plan):
            began = time.perf_counter()
            try:
                return original_predictor.predict(graph, plan)
            finally:
                prediction_work["calls"] += 1
                prediction_work["seconds"] += time.perf_counter()-began
        def __getattr__(self, name):
            return getattr(original_predictor, name)
    if predictor is not None:
        predictor = MeasuredPredictor()
    calls, cost, used, ledger = 0, 0.0, set(), []
    acquisitions = []
    current = copy.deepcopy(graph)
    while True:
        ranked = candidate_ranker(current, plans, policy, predictor)
        selected = next((r for r in ranked if r["executable"]), None)
        if ledger and 'candidate_plan_after' not in ledger[-1]:
            ledger[-1]['candidate_plan_after'] = selected['plan_id'] if selected else None
            ledger[-1]['candidate_order_after'] = [r['plan_id'] for r in ranked]
        if strategy == "none":
            break
        remaining = budget.max_seconds - (clock()-started)
        eligible = [p for p in probes if p.id not in used and p.cost + cost <= budget.max_cost
                    and p.timeout <= remaining and probe_adds_missing_evidence(current,p)]
        if calls >= budget.max_calls or not eligible:
            break
        acquisition_started, prior_calls = clock(), prediction_work['calls']
        chosen, acquisition = choose_probe(current, plans, policy, predictor, eligible, strategy=strategy, seed=seed+calls,fixed_order=fixed_order,candidate_ranker=candidate_ranker)
        acquisition_record = {'seconds':clock()-acquisition_started,
                              'predictor_calls':prediction_work['calls']-prior_calls,
                              'chosen_probe':chosen.id if chosen else None}
        acquisitions.append(acquisition_record)
        if chosen is None:
            break
        # Acquisition itself can be expensive; do not launch a read on an expired budget.
        remaining = budget.max_seconds - (clock()-started)
        if chosen.timeout > remaining or chosen.cost+cost > budget.max_cost or calls >= budget.max_calls:
            acquisition_record['query_skipped'] = 'insufficient_budget_after_acquisition'
            break
        calls += 1
        cost += chosen.cost
        used.add(chosen.id)
        query_start = clock()
        row = {"probe_id": chosen.id, "cost": chosen.cost, "acquisition": acquisition,
               'selected_plan_before':selected['plan_id'] if selected else None,
               'candidate_order_before':[r['plan_id'] for r in ranked]}
        try:
            fact = observe(chosen, current)
            now = wall_clock()
            if isinstance(fact, GraphObservation):
                if chosen.kind not in ("graph_json", "native_graph") or any(f.origin != "observed" for f in fact.facts+fact.relations):
                    raise ValueError("unregistered structural observation")
                current = merge_observation(current, fact, now)
                row.update(status="observed", new_entities=[e.id for e in fact.entities],
                           relation_updates=len(fact.relations), fact_ids=[f.id for f in fact.facts])
            else:
                if (fact.entity, fact.variable) != (chosen.entity, chosen.variable) or fact.origin not in ("observed", "configuration"):
                    raise ValueError("probe returned an unregistered variable or non-observation")
                if not fact.usable(now, current.entity(fact.entity).uid):
                    raise ValueError("probe evidence stale, unknown or bound to another resource")
                updated = copy.deepcopy(current)
                updated.at = now
                updated.facts.append(fact)
                updated.__post_init__()
                current = updated
                row.update(status="observed", fact_id=fact.id)
        except Exception as exc:
            row.update(status="failed", error=type(exc).__name__)
        row["seconds"] = clock()-query_start
        ledger.append(row)
        if clock()-started > budget.max_seconds or row["seconds"] > chosen.timeout:
            ledger[-1]["status"] = "budget_exceeded"
            break
    ranked = candidate_ranker(current, plans, policy, predictor)
    over_budget = clock()-started > budget.max_seconds or any(r["status"] == "budget_exceeded" for r in ledger)
    selected = None if over_budget else next((r for r in ranked if r["executable"]), None)
    if ledger and 'candidate_plan_after' not in ledger[-1]:
        ledger[-1]['candidate_plan_after'] = selected['plan_id'] if selected else None
        ledger[-1]['candidate_order_after'] = [r['plan_id'] for r in ranked]
    chosen_plan = next((p for p in plans if selected and p.id == selected["plan_id"]), None)
    return {"schema": "e2r_intervention_decision_v1", "decision": ("execute" if chosen_plan.actions else "no_action") if chosen_plan else "abstain",
            "selected_plan": selected["plan_id"] if selected else None,
            "candidates": ranked, "observations": ledger, "graph": current.to_dict(),
            "budget": {"calls": calls, "cost": cost, "seconds": clock()-started, "limits": asdict(budget)},
            'predictor_work':prediction_work, 'acquisition_work':acquisitions,
            'candidate_order_final':[r['plan_id'] for r in ranked], 'budget_exceeded':over_budget,
            "observed_verification": {"status": "not_executed"},
            "token_usage": {"input_tokens": 0, "output_tokens": 0, "source": "zero"}}


def execution_deviations(plan_id, forecast, verification):
    mapping = {"endpoints_ready": verification.get("endpoints_status"),
               "business_recovered": verification.get("business", {}).get("status"),
               "non_target_safe": verification.get("non_target_safety"),
               "mechanism_eliminated": verification.get("mechanism_eliminated")}
    return [asdict(Witness(-1, effect, "deviation", observed_deviation={"plan_id": plan_id,
                   "predicted_probability": forecast["probabilities"].get(effect), "observed": status}, level="execution"))
            for effect, status in mapping.items() if status in ("pass", "fail")
            and forecast["probabilities"].get(effect) is not None
            and abs(forecast["probabilities"][effect] - float(status == "pass")) >= 0.5]
