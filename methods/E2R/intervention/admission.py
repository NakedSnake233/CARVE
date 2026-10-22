"""Empirical validation admission cells; no per-candidate probability guarantee."""
from collections import defaultdict
import json
import statistics
from .data import group_key,wilson,validate_record
from .evaluation import observed_plan_signature
from .protocols import protocol_key,anchor_key
from .schema import input_bundle
from .learning_views import record_for_view

BIN_EDGES=(0.,.2,.4,.6,.8,.9,1.)


def score_bin(value,edges=BIN_EDGES):
    if value is None or not 0<=value<=1:return None
    return min(len(edges)-2,next((i for i in range(len(edges)-1) if edges[i]<=value<edges[i+1]),len(edges)-2))


def cell_key(signature,probabilities,edges=BIN_EDGES):
    left=score_bin(probabilities.get('business_recovered'),edges);right=score_bin(probabilities.get('non_target_safe'),edges)
    return json.dumps([signature,left,right],separators=(',',':')) if left is not None and right is not None else None


def fit_admission(rows,predictor,*,calibration_groups,min_attempts=5,min_groups=2,edges=BIN_EDGES,
                  observation_scope=None,registry=None):
    """Bin every input-bearing attempt using its own pre-action forecast.

    Missing-input attempts have unknown scores. Conservatively include each in
    every populated cell for its registered action signature (once per cell).
    This duplicated uncertainty is disclosed, never treated as independent data.
    A separate policy partition must validate the final selector/acquisition.
    """
    allowed=set(calibration_groups)
    if not allowed or any(group_key(r) not in allowed for r in rows):raise ValueError('calibration rows outside declared groups')
    fit_groups=set(predictor.metadata['split']['train'])|set(predictor.metadata['split']['development'])
    if allowed & fit_groups:raise ValueError('calibration groups overlap fitting/checkpoint selection')
    if list(edges)!=sorted(set(edges)) or edges[0]!=0 or edges[-1]!=1:raise ValueError('invalid fixed bins')
    if len({r['episode_id'] for r in rows})!=len(rows):raise ValueError('duplicate calibration attempt identity')
    if observation_scope is not None:
        from .policy_qualification import check_registry,source_scope
        if min_attempts<5 or min_groups<2 or tuple(edges)!=BIN_EDGES:raise ValueError('scoped calibration must retain preregistered evidence floors/bins')
        if registry is None:raise ValueError('scoped calibration needs a partition registry')
        check_registry(registry,predictor.metadata,allowed)
        cached={}
        for row in rows:
            validate_record(row)
            if registry[group_key(row)]['comparison_group']!=row['application']+'/'+row['comparison_group']:
                raise ValueError('calibration comparison group differs from registry')
            source=row.get('source_provenance',{}).get('source_directory')
            if source not in cached:cached[source]=source_scope(row)
            if cached[source]!=observation_scope:raise ValueError('calibration acquisition protocol differs')
    if predictor.metadata.get('input_view','full')=='initial':
        for row in rows:validate_record(row,allow_synthetic=row['source']=='synthetic')
    assigned=defaultdict(list);signature_cells=defaultdict(set);unknown_scores=[];unassigned=[]
    for row in rows:
        signature=observed_plan_signature(row)
        if signature is None:
            unassigned.append(row['episode_id']);continue
        if 'input' not in row:
            unknown_scores.append((signature,row));continue
        viewed=record_for_view(row,predictor.metadata.get('input_view','full'))
        graph,plans=input_bundle(viewed['input']);plan=next(p for p in plans if p.id==row['chosen_plan'])
        forecast=predictor.predict(graph,plan);key=cell_key(signature,forecast['probabilities'],edges)
        if key is None:
            unknown_scores.append((signature,row));continue
        assigned[key].append(row);signature_cells[signature].add(key)
    unknown_assignments=0
    for signature,row in unknown_scores:
        keys=signature_cells[signature]
        if not keys:unassigned.append(row['episode_id'])
        for key in keys:assigned[key].append(row);unknown_assignments+=1
    buckets=defaultdict(list)
    uncertain_membership={row['episode_id'] for _,row in unknown_scores}
    for key,samples in assigned.items():
        arms=defaultdict(list)
        for row in samples:arms[(group_key(row),row['condition_id'],protocol_key(row))].append(row)
        for (group,condition,protocol),arm in arms.items():
            business=[None if r['episode_id'] in uncertain_membership else r.get('outcome',{}).get('effects',{}).get('business_recovered') for r in arm]
            safe=[None if r['episode_id'] in uncertain_membership else r.get('outcome',{}).get('effects',{}).get('non_target_safe') for r in arm]
            n=len(arm);recovered=sum(v is True for v in business);unsafe=sum(v is False for v in safe);unknown_safe=sum(v is None for v in safe)
            buckets[key].append({'group':group,'condition':condition,'protocol_stratum':protocol,'attempts':n,
                'recovery_true':recovered,'recovery_unknown':sum(v is None for v in business),'unsafe':unsafe,'safety_unknown':unknown_safe,
                'recovery_lower':recovered/n,'harm_upper':(unsafe+unknown_safe)/n,
                'attempt_ids':[r['episode_id'] for r in arm],
                'unknown_score_membership_attempt_ids':[r['episode_id'] for r in arm if r['episode_id'] in uncertain_membership]})
    cells={}
    for key,arms in buckets.items():
        groups=defaultdict(lambda:defaultdict(list))
        for arm in arms:groups[arm['group']][arm['condition']].append(arm)
        def average(metric):
            return statistics.mean(statistics.mean(statistics.mean(r[metric] for r in proto) for proto in conditions.values()) for conditions in groups.values())
        n=sum(r['attempts'] for r in arms);yes=sum(r['recovery_true'] for r in arms);bad=sum(r['unsafe']+r['safety_unknown'] for r in arms)
        cells[key]={'schema':'validation_empirical_admission_v2','status':'supported' if n>=min_attempts and len(groups)>=min_groups else 'insufficient_evidence',
            'attempts':n,'groups':len(groups),'group_ids':sorted(groups),'recovery_lower':average('recovery_lower'),'harm_upper':average('harm_upper'),
            'sampling_intervals_descriptive_independence_assumption':{'recovery':wilson(yes,n),'harm_unknown_as_failure':wilson(bad,n)},
            'arms':arms,'bounds_semantics':'unknown-inclusive empirical bounds, equal group/condition/protocol weights; not sampling confidence bounds'}
    return {'schema':'validation_empirical_admission_v2','calibration_groups':sorted(allowed),'bin_edges':list(edges),
        'observation_scope':observation_scope,
        'min_attempts':min_attempts,'min_groups':min_groups,'cells':cells,'unassigned_attempt_ids':unassigned,
        'attempts':len(rows),'unknown_score_attempts':len(unknown_scores),'unknown_score_cell_assignments':unknown_assignments,
        'unknown_score_handling':'conservative inclusion in every populated same-signature cell, not extra independent samples',
        'prediction_seeds':predictor.selected_seeds,'training_configuration':predictor.metadata,
        'policy_validation_required':True,'scope':'candidate empirical admission only; complete selector and each acquisition strategy require independent policy validation',
        'interval_caveat':'Repeated attempts share configuration groups; binomial intervals are descriptive only and never used as a population safety certificate.'}


def lookup_admission(ledger,signature,probabilities):
    key=cell_key(signature,probabilities,ledger['bin_edges'])
    if key not in ledger['cells']:return {'schema':'validation_empirical_admission_v2','status':'uncovered_cell','cell':key}
    cell=ledger['cells'][key]
    return {**{k:v for k,v in cell.items() if k!='arms'},'cell':key,'calibration_groups':ledger['calibration_groups']}
