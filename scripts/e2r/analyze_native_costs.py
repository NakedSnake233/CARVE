#!/usr/bin/env python3
"""Audit attempt denominators, nested timings and accepted-write preconditions."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.contracts import Policy,check_plan,feasible
from methods.E2R.intervention.data import read_records,group_key,validate_record
from methods.E2R.intervention.protocols import protocol_key
from methods.E2R.intervention.schema import EvidenceGraph,Plan,EFFECTS


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit(rows):
    top=defaultdict(list);nested=defaultdict(list);failures=Counter();examples=defaultdict(list)
    invalid_labels=[];checked_writes=0;missing_refresh=[];violations=[];incomparable=[];stages=Counter()
    effect_counts={effect:Counter() for effect in EFFECTS};outcome_ledger=[]
    current_contract=digest(ROOT/'methods/E2R/intervention/contracts.py')
    for row in rows:
        validate_record(row)
        for key,value in row.get('timings_seconds',{}).items():top[key].append(value)
        execution=row.get('execution',{})
        for key,value in execution.get('timings_seconds',{}).items():nested[key].append(value)
        failures[row['status']]+=1
        journal=Path(row['artifact_directory'])/'execution_private.jsonl'
        prepared=set()
        if journal.exists():
            for line in journal.read_text().splitlines():
                event=json.loads(line)
                if event.get('event')=='prepared' and not event.get('dry_run',False):prepared.add(event['step'])
        prepared.update(step['index'] for step in execution.get('steps',[]) if step.get('write_verified'))
        for effect in EFFECTS:
            value=row.get('outcome',{}).get('effects',{}).get(effect)
            effect_counts[effect]['unknown' if value is None else 'true' if value else 'false']+=1
        outcome_ledger.append({'attempt_id':row['episode_id'],'original_slot':row['original_schedule_slot'],
            'group':group_key(row),'protocol_stratum':protocol_key(row),'status':row['status'],
            'effects':row.get('outcome',{}).get('effects',{}),'prepared_or_verified_steps':len(prepared),
            'execution_status':execution.get('status'),'artifact':row['artifact_directory']})
        if row['status'] not in ('observed',):
            category=('pre_input' if 'input' not in row else 'pre_execution' if not prepared
                      else 'partial_or_complete_execution_or_verification')
            stages[category]+=1
            if len(examples[category])<12:examples[category].append({'attempt':row['episode_id'],'artifact':row['artifact_directory'],'status':row['status'],'detail':row.get('detail')})
        if execution.get('status') not in ('executed','no_action','controller_failed') and any(
                v is not None for v in row.get('outcome',{}).get('effects',{}).values()):invalid_labels.append(row['episode_id'])
        if 'input' not in row:continue
        plan=next(Plan.from_dict(p) for p in row['input']['plans'] if p['id']==row['chosen_plan'])
        source_contract=Path(row['source_provenance']['source_directory'])/'source_snapshot/contracts.py'
        refreshes={r['step']:r for r in execution.get('refreshes',[])}
        for index in sorted(prepared):
            if not source_contract.exists() or digest(source_contract)!=current_contract:
                incomparable.append({'attempt':row['episode_id'],'step':index,'reason':'historical_contract_implementation_differs'});continue
            if index not in refreshes:
                missing_refresh.append({'attempt':row['episode_id'],'step':index});continue
            fresh=EvidenceGraph.from_dict(refreshes[index]['graph'])
            checks=check_plan(fresh,Plan(plan.id,(plan.actions[index],)),Policy.from_dict(row['operation_policy']))
            checked_writes+=1
            if not feasible(checks,allow_deferred=False):
                violations.append({'attempt':row['episode_id'],'step':index,'artifact':row['artifact_directory'],
                                   'checks':[{'predicate':c.predicate,'status':c.status} for c in checks]})
    def summarize(values):
        values=sorted(values)
        return {'n':len(values),'total':sum(values),'mean':statistics.mean(values),
                'median':statistics.median(values),'p95':values[min(len(values)-1,int(.95*len(values)))]}
    return {'status':'complete','attempts':len(rows),'original_slots':len({(r['application'],r['group_id'],r['original_schedule_slot']) for r in rows}),
        'groups':len({group_key(r) for r in rows}),'statuses':dict(failures),'failure_stages':dict(stages),'examples':dict(examples),
        'effect_denominators':{key:{'attempts':len(rows),**{kind:counts[kind] for kind in ('true','false','unknown')}} for key,counts in effect_counts.items()},
        'attempt_outcome_ledger':outcome_ledger,
        'status_semantics':'observed means outcome recording completed; it does not mean recovery or safety succeeded',
        'historical_episode_restore_failures':[{'attempt':r['episode_id'],'artifact':r['artifact_directory']}
                                             for r in rows if r.get('restore',{}).get('status')!='restored'],
        'unexecuted_attempts_with_known_effect_labels':invalid_labels,
        'attempted_write_precondition_audit':{'checked_steps':checked_writes,'violations':violations,
            'missing_refresh_evidence':missing_refresh,'historical_contracts_not_recomputed':incomparable,
            'scope':'Union of non-dry-run prepared journal steps and verified writes; use each mandatory-refresh graph and its operation policy, never the older initial input'},
        'top_level_timings':{k:summarize(v) for k,v in top.items()},
        'nested_execution_timings':{k:summarize(v) for k,v in nested.items()},
        'timing_caveats':['total_attempt contains other top-level phases; never add it to them.',
            'execution_and_controller contains nested precondition/write/controller timings; report nested breakdown separately.',
            'Separate compensation, fixed wait, and environment reset clocks are not consistently available; mark unmeasured/combined.',
            'Prediction timing includes tensorization and predictor execution; it is not pure neural forward time.'],
        'unmeasured_components':['standalone_fixed_wait','standalone_compensation','standalone_environment_reset_excluding_compensation'],
        'protocol_attempt_counts':dict(Counter(protocol_key(r) for r in rows))}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=False);result=audit(read_records(a.data));result['completed_at']=time.time()
    (a.out/'B5_COST_FAILURE.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    (a.out/'B5_DONE.json').write_text(json.dumps({'status':'complete','data':str(a.data.resolve()),'data_sha256':digest(a.data),'report':str((a.out/'B5_COST_FAILURE.json').resolve()),'completed_at':time.time()},indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('status','attempts','original_slots','statuses','unexecuted_attempts_with_known_effect_labels')}))
