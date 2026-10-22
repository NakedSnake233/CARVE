"""Outcome-grounded metrics and paired configuration-group comparisons."""
from __future__ import annotations

import math
import random
import statistics
import time
from collections import Counter, defaultdict
from .data import distinguish_pairs, group_key, validate_record
from .schema import EFFECTS
from .evaluation import summarize_decisions
from .protocols import decision_key
from .protocols import protocol_key


def prediction_metrics(rows, predictor, minimum_repeats=5):
    predictions, pairs, records = [], defaultdict(list), []
    times, sizes = [], []
    for row in rows:
        graph, plan = validate_record(row, allow_synthetic=row['source']=='synthetic')
        if graph is None:
            predictions.append(None)
            records.append({'attempt_id':row['episode_id'],'group':group_key(row),'protocol_stratum':protocol_key(row),
                'status':row['status'],'probabilities':{k:None for k in EFFECTS},'targets':{k:None for k in EFFECTS}})
            continue
        started = time.perf_counter()
        forecast = predictor.predict(graph, plan)
        times.append(time.perf_counter()-started)
        predictions.append(forecast)
        records.append({'attempt_id':row['episode_id'],'group':group_key(row),'protocol_stratum':protocol_key(row),
            'status':row['status'],'probabilities':forecast['probabilities'],'targets':row['outcome']['effects'],
            'unseen_signature':forecast.get('unseen_signature'),'outside_training_range':forecast.get('outside_training_range'),
            'graph_sizes':forecast.get('graph_sizes',[])})
        sizes.extend(forecast.get('graph_sizes',[]))
        for effect in EFFECTS:
            y, p = row['outcome']['effects'][effect], forecast['probabilities'].get(effect)
            if y is not None and p is not None:
                pairs[effect].append((float(y),float(p)))
    metrics = {}
    for effect in EFFECTS:
        known = pairs[effect]
        bins = []
        for index in range(10):
            values = [(y,p) for y,p in known if min(9,int(p*10))==index]
            bins.append({'bin':index,'n':len(values),'observed':statistics.mean(y for y,p in values) if values else None,
                         'predicted':statistics.mean(p for y,p in values) if values else None})
        metrics[effect] = {
            'known':len(known), 'attempts':len(rows), 'unknown_or_unpredicted':len(rows)-len(known),
            'brier':statistics.mean((y-p)**2 for y,p in known) if known else None,
            'bce':statistics.mean(-y*math.log(max(1e-7,p))-(1-y)*math.log(max(1e-7,1-p)) for y,p in known) if known else None,
            'ece_10_equal_width':sum(b['n']*abs(b['observed']-b['predicted']) for b in bins if b['n'])/len(known) if known else None,
            'calibration_bins':bins}
    supported = distinguish_pairs(rows,minimum_repeats)
    discrimination = []
    for pair in supported:
        left,right=predictions[pair['left']],predictions[pair['right']]
        if left is None or right is None:
            continue
        differences=[abs(left['probabilities'][k]-right['probabilities'][k]) for k in EFFECTS
                     if left['probabilities'][k] is not None and right['probabilities'][k] is not None]
        if not differences:
            continue
        same_diagnosis = rows[pair['left']].get('diagnosis_label') == rows[pair['right']].get('diagnosis_label')
        discrimination.append({**pair,'same_fine_diagnosis':same_diagnosis,'predicted_distance':statistics.mean(differences),
            'predicted_different':max(differences)>.25,'correct':(max(differences)>.25)==(pair['kind']=='different')})
    costs=defaultdict(list)
    for row in rows:
        for key,value in row.get('timings_seconds',{}).items():costs[key].append(value)
        execution=row.get('execution',{})
        for key,value in execution.get('timings_seconds',{}).items():
            costs['execution.'+key].append(value)
    by_kind={kind:[p for p in discrimination if p['kind']==kind] for kind in ('different','equivalent')}
    return {'schema':'intervention_prediction_metrics_v1','attempts':len(rows),'independent_groups':len({group_key(r) for r in rows}),
        'predictions':records,
        'effects':metrics,'distinguishability':{'threshold':.25,'kind_counts':dict(Counter(p['kind'] for p in supported)),
            'same_fine_diagnosis_kind_counts':dict(Counter(p['kind'] for p in discrimination if p['same_fine_diagnosis'])),
            'same_fine_diagnosis_accuracy':statistics.mean(p['correct'] for p in discrimination if p['same_fine_diagnosis'])
                if any(p['same_fine_diagnosis'] for p in discrimination) else None,
            'scored_pairs':len(discrimination),'accuracy':statistics.mean(p['correct'] for p in discrimination) if discrimination else None,
            'always_different_accuracy':statistics.mean(p['kind']=='different' for p in discrimination) if discrimination else None,
            'always_equivalent_accuracy':statistics.mean(p['kind']=='equivalent' for p in discrimination) if discrimination else None,
            'kind_balanced_accuracy':statistics.mean(statistics.mean(p['correct'] for p in values) for values in by_kind.values())
                if all(by_kind.values()) else None,
            'distance_mae':statistics.mean(abs(p['distance']-p['predicted_distance']) for p in discrimination) if discrimination else None,
            'pairs':discrimination,'scope':'statistically supported repeated-condition pairs; no single-trial difference labels'},
        'mean_inference_seconds':statistics.mean(times) if times else None,
        'mean_retained_nodes':statistics.mean(s['retained'] for s in sizes) if sizes else None,
        'mean_total_nodes':statistics.mean(s['total'] for s in sizes) if sizes else None,
        'mean_retained_edges':statistics.mean(s['edges'] for s in sizes) if sizes else None,
        'cost_seconds':{k:{'n':len(v),'mean':statistics.mean(v),'total':sum(v)} for k,v in costs.items()},
        'failures':dict(Counter(r['status'] for r in rows)),
        'restore_failures':sum(r.get('restore',{}).get('status')!='restored' for r in rows)}


def paired_prediction_difference(left, right, effect='business_recovered', *, seed=42, repetitions=2000):
    """Pair identical attempts, then bootstrap whole configuration groups."""
    a={r['attempt_id']:r for r in left};b={r['attempt_id']:r for r in right}
    if len(a)!=len(left) or len(b)!=len(right) or set(a)!=set(b):
        raise ValueError('prediction comparison requires unique identical attempt IDs')
    groups=defaultdict(list);eligible=0
    for identity in a:
        x,y=a[identity],b[identity]
        if x['group']!=y['group'] or x['targets']!=y['targets'] or x['protocol_stratum']!=y['protocol_stratum']:
            raise ValueError('paired prediction provenance/target mismatch')
        target,p,q=x['targets'].get(effect),x['probabilities'].get(effect),y['probabilities'].get(effect)
        if target is not None and p is not None and q is not None:
            groups[x['group']].append((float(target)-p)**2-(float(target)-q)**2);eligible+=1
    means={g:statistics.mean(v) for g,v in groups.items()};keys=sorted(means)
    result={'effect':effect,'raw_attempts':len(a),'common_known_predictions':eligible,
            'excluded_unknown_or_unpredicted':len(a)-eligible,'groups':len(keys),
            'macro_group_brier_difference':statistics.mean(means.values()) if means else None,
            'scope':'left minus right on identical known-target/predicted attempts; equal weight per configuration group'}
    if len(keys)<2:
        result['status']='insufficient_independent_groups';return result
    rng=random.Random(seed);values=sorted(statistics.mean(means[rng.choice(keys)] for _ in keys) for _ in range(repetitions))
    result.update(status='paired_group_bootstrap',seed=seed,repetitions=repetitions,
                  ci95=[values[int(.025*repetitions)],values[min(repetitions-1,int(.975*repetitions))]])
    return result


def paired_group_difference(left, right, left_threshold, right_threshold, *, seed=42, repetitions=2000):
    """Resample shared groups, keeping each group's conditions/arms together."""
    l={decision_key(r):r for r in left}
    r={decision_key(r):r for r in right}
    if len(l)!=len(left) or len(r)!=len(right):
        raise ValueError('duplicate decision identity in paired comparison')
    if set(l)!=set(r):
        raise ValueError('paired comparison requires identical attempted conditions')
    grouped=defaultdict(list)
    for key in sorted(l):grouped[key[0]].append(key)
    keys=sorted(grouped)
    metrics=('population_recovery_lower','policy_recovery_lower','policy_recovery_upper','coverage','risk_upper')
    def difference(chosen):
        identities=[key for group in chosen for key in grouped[group]]
        a=summarize_decisions([l[k] for k in identities],left_threshold)
        b=summarize_decisions([r[k] for k in identities],right_threshold)
        return {k:a[k]-b[k] if a[k] is not None and b[k] is not None else None for k in metrics}
    observed=difference(keys)
    left_summary=summarize_decisions(left,left_threshold)
    right_summary=summarize_decisions(right,right_threshold)
    bounds = ([left_summary['policy_recovery_lower']-right_summary['policy_recovery_upper'],
               left_summary['policy_recovery_upper']-right_summary['policy_recovery_lower']]
              if left_summary['policy_recovery_lower'] is not None and right_summary['policy_recovery_lower'] is not None else None)
    if len(keys)<2:
        return {'status':'insufficient_independent_groups','groups':len(keys),'difference':observed,
                'policy_value_difference_bounds':bounds}
    rng=random.Random(seed); samples=defaultdict(list)
    for _ in range(repetitions):
        delta=difference([rng.choice(keys) for _ in keys])
        for key,value in delta.items():
            if value is not None:samples[key].append(value)
    intervals={}
    for key,values in samples.items():
        values.sort();intervals[key]=[values[int(.025*len(values))],values[min(len(values)-1,int(.975*len(values)))]]
    return {'status':'paired_configuration_group_bootstrap','groups':len(keys),
            'conditions':sum(row.get('condition_weight',1.0) for row in left),'protocol_cells':len(l),
            'repetitions':repetitions,'seed':seed,'difference':observed,'ci95':intervals,
            'policy_value_difference_bounds':bounds,
            'valid_bootstrap_counts':{k:len(v) for k,v in samples.items()},
            'note':'Intervals are descriptive across measured groups. Lower-minus-lower is a difference of lower-bound metrics, not a conservative bound on the true policy difference; see policy_value_difference_bounds. Repeated runs are not independent groups.'}
