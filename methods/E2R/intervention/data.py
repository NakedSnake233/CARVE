"""Intervention records, whole-group splits, and repeated-outcome pair supervision."""
from __future__ import annotations

import itertools
import json
import math
import random
from collections import defaultdict
from pathlib import Path

from .schema import EFFECTS, input_bundle, number
from .model import plan_signature
from .protocols import protocol_key, anchor_key


def validate_record(row: dict, *, allow_synthetic=False):
    if row.get("schema") == "e2r_intervention_attempt_v1":
        needed = {"episode_id", "group_id", "application", "condition_id", "comparison_group", "chosen_plan", "source", "status"}
        if not needed <= row.keys() or row["status"] == "observed" or "input" in row:
            raise ValueError("malformed failed-attempt record")
        if row["source"] not in ("live", "synthetic") or row["source"] == "synthetic" and not allow_synthetic:
            raise ValueError("synthetic attempt requires explicit opt-in")
        return None, None
    needed = {"schema", "episode_id", "group_id", "application", "condition_id", "comparison_group",
              "source", "input", "chosen_plan", "action_started_at", "outcome", "execution"}
    if not needed <= set(row) or row["schema"] != "e2r_intervention_episode_v1":
        raise ValueError("incomplete intervention record; RCA-only observations cannot train effects")
    if row["source"] not in ("live", "synthetic") or row["source"] == "synthetic" and not allow_synthetic:
        raise ValueError("synthetic data requires explicit engineering-test opt-in")
    graph, plans = input_bundle(row["input"])
    plan = next((p for p in plans if p.id == row["chosen_plan"]), None)
    if plan is None or row["execution"].get("plan_id") != row["chosen_plan"]:
        raise ValueError("executed action is not the recorded candidate")
    outcome = row["outcome"]
    if (not number(row["action_started_at"]) or not number(outcome.get("observed_at"))
            or not graph.at <= row["action_started_at"] <= outcome["observed_at"]):
        raise ValueError("pre-action/after-action temporal boundary violated")
    effects = outcome.get("effects", {})
    if set(effects) != set(EFFECTS) or any(type(v) not in (bool, type(None)) for v in effects.values()):
        raise ValueError("effects must be boolean/unknown verification labels")
    steps = row["execution"].get("steps",[])
    complete_writes_with_failed_controller = (row["execution"].get("status")=="controller_failed"
        and row["execution"].get("all_writes_applied") is True and bool(plan.actions) and len(steps)==len(plan.actions)
        and all(step.get("controller_converged") is True for step in steps[:-1])
        and steps[-1].get("controller_converged") is False
        and all(step.get("write_verified") is True and step.get("index")==i
                and step.get("operation")==action.operation and step.get("target")==action.target
                for i,(step,action) in enumerate(zip(steps,plan.actions))))
    if row["execution"].get("status") not in ("executed", "no_action") and not complete_writes_with_failed_controller and any(v is not None for v in effects.values()):
        raise ValueError("unexecuted/unknown interventions cannot receive known effect labels")
    if row["source"] == "live":
        verification = outcome.get("verification")
        if not isinstance(verification, dict):
            raise ValueError("live effect labels need an independent verification package")
        if effects["business_recovered"] is not None or effects["non_target_safe"] is not None:
            from rca_bench.recovery_oracle import WorkloadWindow
            from .verification import VerificationProtocol, business_windows, conjunction
            if not all(k in outcome for k in ("verification_protocol", "baseline_windows", "execution_completed_at")):
                raise ValueError("live business labels require reproducible raw window evidence")
            protocol = VerificationProtocol(**outcome["verification_protocol"])
            bundles = {"business": verification.get("business", {}), **verification.get("non_target", {})}
            statuses = {}
            for name, package in bundles.items():
                baseline = WorkloadWindow(**outcome["baseline_windows"][name])
                windows = [WorkloadWindow(**w) for w in package.get("raw_windows", [])]
                recomputed = business_windows(baseline, windows, completed_at=outcome["execution_completed_at"],
                      evaluated_at=package["evaluated_at"], protocol=protocol)
                if recomputed["status"] != package.get("status"):
                    raise ValueError("business label cannot be reproduced from its measured windows")
                statuses[name] = recomputed["status"]
            safety_statuses = [v for name,v in statuses.items() if name!="business"]
            from .verification import execution_overlap_windows
            for interval_kind in ('during_execution_non_target','during_strategy_non_target'):
                for name,package in verification.get(interval_kind,{}).items():
                    start,end=outcome.get('action_started_at'),outcome['execution_completed_at']
                    if interval_kind=='during_strategy_non_target' and 'strategy_started_at' in outcome:
                        start,end=outcome['strategy_started_at'],outcome.get('strategy_completed_at')
                        masked_at=row.get('masked_graph',{}).get('at')
                        if (not all(number(v) for v in (start,end,masked_at))
                                or not masked_at<=start<=end<=row['action_started_at']):
                            raise ValueError('observation strategy interval does not precede the action')
                    else:
                        # Historical forced-arm collectors called their execution
                        # interval during_strategy; preserve that declared meaning.
                        if interval_kind=='during_strategy_non_target' and row.get('assigned_method') is not None:
                            raise ValueError('adaptive strategy safety requires explicit strategy timestamps')
                        if not number(start) or not row['action_started_at']<=start<=end:
                            raise ValueError('execution safety interval is not within the recorded action')
                    recomputed = execution_overlap_windows(WorkloadWindow(**outcome["baseline_windows"][name]),
                        [WorkloadWindow(**w) for w in package.get("raw_windows",[])],started_at=start,
                        completed_at=end,evaluated_at=package["evaluated_at"],protocol=protocol)
                    if recomputed["status"] != package.get("status"):
                        raise ValueError("execution/strategy safety label cannot be reproduced")
                    safety_statuses.append(recomputed["status"])
            if conjunction(safety_statuses) != verification.get("non_target_safety"):
                raise ValueError("non-target safety label lacks corresponding workload evidence")
        mapping = {"endpoints_ready": verification.get("endpoints_status"),
                   "capacity_sufficient": verification.get("capacity_status"),
                   "business_recovered": verification.get("business", {}).get("status"),
                   "non_target_safe": verification.get("non_target_safety"),
                   "mechanism_eliminated": verification.get("mechanism_eliminated")}
        for key, status in mapping.items():
            expected = True if status == "pass" else False if status == "fail" else None
            if effects[key] is not expected:
                raise ValueError("effect label disagrees with independent verification")
    return graph, plan


def read_records(path, *, allow_synthetic=False):
    rows = [json.loads(s) for s in Path(path).read_text().splitlines() if s.strip()]
    if not rows or len({r.get("episode_id") for r in rows}) != len(rows):
        raise ValueError("empty data or duplicate episode identifiers")
    for row in rows:
        validate_record(row, allow_synthetic=allow_synthetic)
    if len({r["source"] for r in rows}) != 1:
        raise ValueError("live and synthetic interventions must not be mixed")
    return rows


def group_key(row):
    return row["application"] + "/" + row["group_id"]


def grouped_split(rows, seed=42, heldout_application=None):
    groups = sorted({group_key(r) for r in rows})
    random.Random(seed).shuffle(groups)
    if heldout_application:
        test = [g for g in groups if g.startswith(heldout_application + "/")]
        rest = [g for g in groups if g not in test]
        nval = max(1, round(len(rest) * 0.2))
        split = {"train": rest[nval:], "validation": rest[:nval], "test": test}
    else:
        if len(groups) < 5:
            raise ValueError("need at least five independent configuration groups")
        ntrain, nval = max(1, int(len(groups) * 0.6)), max(1, int(len(groups) * 0.2))
        split = {"train": groups[:ntrain], "validation": groups[ntrain:ntrain+nval], "test": groups[ntrain+nval:]}
    validate_split(rows, split)
    return split


def validate_split(rows, split):
    if set(split) != {"train", "validation", "test"} or any(not split[k] for k in split):
        raise ValueError("all three group partitions must be nonempty")
    flat = [g for groups in split.values() for g in groups]
    if len(flat) != len(set(flat)) or set(flat) != {group_key(r) for r in rows}:
        raise ValueError("group split overlaps or omits data")
    # Explicit paired experiment identifiers cannot cross partitions, even if group IDs differ.
    partition = {g: part for part, groups in split.items() for g in groups}
    paired = defaultdict(set)
    for row in rows:
        paired[(row["application"], row["comparison_group"])].add(partition[group_key(row)])
    if any(len(parts) > 1 for parts in paired.values()):
        raise ValueError("paired experiment conditions leak across partitions")


def wilson(successes, n, z=1.96):
    p = successes / n
    denominator = 1 + z*z/n
    center = (p + z*z/(2*n)) / denominator
    radius = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / denominator
    return center-radius, center+radius


def distinguish_pairs(rows, minimum_repeats=5, equivalence_margin=0.25, max_pairs=1024, seed=42, *, protocol_matching=True):
    """Pairs label outcome differences, not root-cause equality or model disagreement."""
    if minimum_repeats < 2:
        raise ValueError("a single success/failure is not repeated-effect supervision")
    groups = defaultdict(list)
    for index, row in enumerate(rows):
        graph, plan = validate_record(row, allow_synthetic=True)
        if graph is None:
            continue
        key = (row["application"], row["comparison_group"], row["condition_id"], plan_signature(graph, plan),
               protocol_key(row, protocol_matching))
        groups[key].append(index)
    summaries = []
    for key, indices in groups.items():
        means, intervals = {}, {}
        for effect in EFFECTS:
            values = [rows[i]["outcome"]["effects"][effect] for i in indices
                      if rows[i]["outcome"]["effects"][effect] is not None]
            if len(values) >= minimum_repeats:
                means[effect], intervals[effect] = sum(values)/len(values), wilson(sum(values), len(values))
        if "business_recovered" in means and "non_target_safe" in means:
            summaries.append((key, indices, means, intervals))
    pairs = []
    for left, right in itertools.combinations(summaries, 2):
        ka, ia, ma, ca = left
        kb, ib, mb, cb = right
        if ka[3] != kb[3] or ka[4] != kb[4]:
            continue
        common = sorted(set(ma) & set(mb))
        different = any(ca[k][1] < cb[k][0] or cb[k][1] < ca[k][0] for k in common)
        equivalent = all(max(abs(ca[k][0]-cb[k][1]), abs(ca[k][1]-cb[k][0])) <= equivalence_margin for k in common)
        comparable = ka[:2] == kb[:2]  # Same preregistered condition family for negative pairs.
        if different and comparable or equivalent:
            pairs.append({"left": min(ia, key=lambda i:anchor_key(rows[i])),
                          "right": min(ib, key=lambda i:anchor_key(rows[i])), 'protocol_stratum':ka[4],
                          "distance": sum(abs(ma[k]-mb[k]) for k in common)/len(common),
                          "kind": "different" if different else "equivalent",
                          "left_repeats": len(ia), "right_repeats": len(ib)})
    random.Random(seed).shuffle(pairs)
    return pairs[:max_pairs]
