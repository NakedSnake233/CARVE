"""Report scopes, protocol sensitivity and actual observation budgets explicitly."""
from __future__ import annotations
import copy
import statistics
from collections import Counter, defaultdict

from .evaluation import summarize_decisions
from .protocols import decision_key
from .schema import EFFECTS


def reweight_conditions(decisions):
    counts=Counter((r['group'],r['condition']) for r in decisions)
    return [{**r,'condition_weight':1/counts[(r['group'],r['condition'])]} for r in decisions]


def summaries_by_protocol(decisions, threshold):
    groups=defaultdict(list)
    for row in decisions:groups[row['protocol_stratum']].append(row)
    return {key:summarize_decisions(reweight_conditions(rows),threshold) for key,rows in sorted(groups.items())}


def prediction_subsets(predictions):
    subsets=defaultdict(list)
    for row in predictions:
        subsets['protocol:'+row['protocol_stratum']].append(row)
        subsets['application:'+row['group'].split('/')[0]].append(row)
        subsets['family:'+row['group'].split('/')[-1].split('-')[0]].append(row)
    report={}
    for name,rows in sorted(subsets.items()):
        effects={}
        for key in EFFECTS:
            pairs=[(r['targets'].get(key),r['probabilities'].get(key)) for r in rows
                   if r['targets'].get(key) is not None and r['probabilities'].get(key) is not None]
            effects[key]={'attempts':len(rows),'known_predictions':len(pairs),'unknown_or_unpredicted':len(rows)-len(pairs),
                          'brier':statistics.mean((float(y)-p)**2 for y,p in pairs) if pairs else None}
        report[name]={'attempts':len(rows),'groups':len({r['group'] for r in rows}),'effects':effects}
    return report


def choose_replay_threshold(validation, max_risk):
    thresholds=sorted({r['score'] for r in validation if r.get('score') is not None}|{1e9})
    allowed=[]
    for value in thresholds:
        summary=summarize_decisions(validation,value)
        risk=summary['risk_upper']
        if risk is None or risk<=max_risk:allowed.append((summary['coverage'] or 0,value,summary))
    return max(allowed,key=lambda r:(r[0],r[1])) if allowed else (0,1e9,summarize_decisions(validation,1e9))


def rejection_summary(decisions, threshold, *, composites_only=False):
    """Report candidate rejections before ranking, with final utility gate separate."""
    instances=[];candidate_cells=0;selected=executed=0
    for decision in decisions:
        candidates=[c for c in decision.get('candidates',[]) if c['action_count']>(1 if composites_only else 0)]
        candidate_cells+=bool(candidates)
        instances.extend(candidates)
        chosen=next((c for c in candidates if c['plan_id']==decision['plan_id']),None)
        selected+=chosen is not None
        executed+=bool(chosen and decision['score'] is not None and decision['score']>=threshold)
    reasons=Counter(reason for c in instances for reason in c['reasons'])
    return {'all_protocol_cells':len(decisions),'cells_without_anchor':sum(d.get('anchor_attempt_id') is None for d in decisions),
            'cells_with_registered_candidates':candidate_cells,'candidate_instances':len(instances),
            'candidate_instances_rejected_before_ranking':sum(not c['executable'] for c in instances),
            'candidate_rejection_reasons':dict(reasons),
            'unseen_signature_candidate_instances':sum(bool(c['empirical_effects'].get('unseen_signature')) for c in instances if c.get('empirical_effects')),
            'selected_candidate_cells_before_validation_gate':selected,'automatically_executed_cells':executed,
            'selected_candidate_cells_refused_by_validation_utility_gate':selected-executed,
            'scope':'registered multi-action candidates' if composites_only else 'registered nonempty candidates',
            'count_unit':'unweighted protocol cells and candidate instances; reasons can overlap'}


def refinement_resources(decisions):
    available=[r for r in decisions if r.get('refinement') is not None]
    budgets=[r['refinement']['budget'] for r in available]
    def values(field):return [b[field] for b in budgets]
    query_seconds=[r['refinement'].get('saved_query_seconds_charged',0.) for r in available]
    predictions=[r['refinement'].get('predictor_work',{}) for r in available]
    actual_calls=Counter(str(b['calls']) for b in budgets)
    total_weight=sum(r.get('condition_weight',1.) for r in decisions)
    observed_weight=sum(r.get('condition_weight',1.) for r in available)
    def weighted(getter, denominator):
        return sum(r.get('condition_weight',1.)*getter(r['refinement']) for r in available)/denominator if denominator else None
    violations=[r for r in available if r['refinement'].get('budget_exceeded') or
        any(r['refinement']['budget'][k]>r['refinement']['budget']['limits']['max_'+k]
            for k in ('calls','cost','seconds'))]
    changed=0
    for r in available:
        for step in r['refinement']['observations']:
            changed+=step.get('selected_plan_before')!=step.get('candidate_plan_after')
    return {'protocol_cells':len(decisions),'cells_without_masked_input':len(decisions)-len(available),
        'cells_with_queries':sum(b['calls']>0 for b in budgets),'cells_without_queries':sum(b['calls']==0 for b in budgets),
        'actual_call_distribution':dict(actual_calls),'budget_violation_cells':len(violations),
        'mean_calls':weighted(lambda r:r['budget']['calls'],total_weight),
        'mean_registered_cost':weighted(lambda r:r['budget']['cost'],total_weight),
        'mean_total_strategy_seconds_among_invoked':weighted(lambda r:r['budget']['seconds'],observed_weight),
        'mean_saved_query_seconds':weighted(lambda r:r.get('saved_query_seconds_charged',0.),total_weight),
        'mean_predictor_calls':weighted(lambda r:r.get('predictor_work',{}).get('calls',0),total_weight),
        'mean_predictor_seconds_among_invoked':weighted(lambda r:r.get('predictor_work',{}).get('seconds',0),observed_weight),
        'resource_weighting':'same group-condition weights as quality; missing-input cells make zero queries, time is reported among invoked strategies',
        'candidate_plan_changes':changed,
        'cells_with_no_matching_arm':sum(not r['matching_arm_present'] for r in decisions),
        'actual_budget_scope':'same predeclared upper bounds; adaptive strategies may stop at different actual spend'}
