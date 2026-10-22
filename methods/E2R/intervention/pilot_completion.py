"""Explicit pilot completion receipts; failed attempts are never relabelled or removed."""
from __future__ import annotations
import copy
import json
from pathlib import Path
from .data import read_records


def inspect_sources(original,amendments):
    original=Path(original)
    protocol=json.loads((original/'protocol.json').read_text())
    order=json.loads((original/'order.json').read_text())
    if len(order)!=30 or protocol.get('schema')!='native_intervention_pilot_v2':raise ValueError('original v2 schedule must have 30 slots')
    all_rows=[];covered={};versions=[]
    for source_index,directory in enumerate([original,*map(Path,amendments)]):
        source_protocol=json.loads((directory/'protocol.json').read_text())
        if source_index:
            for key in ('native_application','experimental_namespace','policy','plans','arms','cases','workloads','verification'):
                if source_protocol[key]!=protocol[key]:raise ValueError('completion changes the original intervention/outcome protocol: '+key)
            assigned=source_protocol['scheduled_jobs']
            versions.append({'directory':str(directory),'baseline_consecutive_windows':source_protocol.get('baseline_consecutive_windows',1),
                             'baseline_health_timeout':source_protocol.get('baseline_health_timeout',0)})
        else:
            assigned=None
            versions.append({'directory':str(directory),'baseline_consecutive_windows':1,'baseline_health_timeout':0})
        raw_rows=read_records(directory/'episodes.jsonl')
        for index,raw in enumerate(raw_rows):
            slot=index if assigned is None else assigned[index]['original_schedule_slot']
            if not 0<=slot<len(order):raise ValueError('completion references an invalid original slot')
            if assigned is not None and slot in covered:raise ValueError('completion must not re-run an already completed slot')
            expected=order[slot]
            if any(raw[k]!=expected[j] for k,j in [('group_id','group_id'),('condition_id','condition_id'),('repeat','repeat'),('chosen_plan','arm')]):
                raise ValueError('completion assignment differs from its original randomized slot')
            if assigned is not None and raw.get('original_schedule_slot')!=slot:raise ValueError('missing explicit original-slot provenance')
            row=copy.deepcopy(raw)
            row['artifact_directory']=str((directory/raw['episode_id']).resolve())
            row['episode_id']=directory.name+'/'+raw['episode_id'];row['original_schedule_slot']=slot
            all_rows.append(row)
            terminal={'observed','rejected','controller_failed','execution_unknown','write_deviation','empirical_recheck_rejected'}
            if raw['status'] in terminal and raw['restore']['status']=='restored' and 'completed_at' in raw:covered[slot]=row
    return protocol,order,all_rows,covered,versions


def complete_pilot(original,amendments,out):
    protocol,order,rows,covered,versions=inspect_sources(original,amendments)
    if len(covered)!=len(order):raise ValueError('pilot still has incomplete original slots')
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    combined={**protocol,'schema':'native_intervention_pilot_completion_v1','original_campaign':str(Path(original).resolve()),
              'amendment_campaigns':[str(Path(a).resolve()) for a in amendments],'baseline_protocol_versions':versions}
    (out/'protocol.json').write_text(json.dumps(combined,indent=2)+'\n')
    with (out/'episodes.jsonl').open('x') as stream:
        for row in rows:stream.write(json.dumps(row,allow_nan=False)+'\n')
    summary={'schema':'native_pilot_completion_v1','status':'complete','planned':len(order),'attempted':len(rows),
             'completed_original_slots':len(covered),'observed':sum(r['status']=='observed' for r in rows),
             'failed_attempts':sum(r['status']=='failed' for r in rows),'original_summary_preserved':True,
             'completion_semantics':'all original randomized slots completed; every failed attempt retained',
             'baseline_protocol_versions':versions}
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');return summary


def verified_pilot(path,application):
    path=Path(path);summary=json.loads(path.read_text());directory=path.parent
    protocol=json.loads((directory/'protocol.json').read_text())
    if (summary.get('status')!='complete' or summary.get('planned')!=30
            or protocol.get('native_application')!=application or protocol.get('experimental_namespace')!='e2r-isg-'+application):
        raise ValueError('matching native pilot has not completed')
    if protocol.get('schema')=='native_intervention_pilot_completion_v1':
        base,order,rows,covered,versions=inspect_sources(protocol['original_campaign'],protocol['amendment_campaigns'])
        if len(covered)!=30 or len(rows)!=summary['attempted']:raise ValueError('pilot completion receipt cannot be reproduced')
        return protocol,list(covered.values()),rows
    if protocol.get('schema')!='native_intervention_pilot_v2' or summary.get('attempted')!=30:
        raise ValueError('matching native v2 pilot or explicit completion receipt required')
    base,order,rows,covered,versions=inspect_sources(directory,[])
    if len(covered)!=30:raise ValueError('not all original pilot slots completed')
    return protocol,list(covered.values()),rows
