#!/usr/bin/env python3
"""Validate and combine complete real campaigns without discarding failed/unknown attempts."""
import argparse
import copy
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.data import read_records,validate_record,validate_split,group_key


def pack(manifest,out):
    spec=json.loads(manifest.read_text());out.mkdir(parents=True,exist_ok=False)
    rows=[];sources=[]
    for entry in spec['campaigns']:
        path=Path(entry['path']);summary=json.loads((path/'summary.json').read_text())
        if summary['status']!='complete' or summary['attempted']!=summary['planned']:
            raise ValueError('campaign not fully attempted: '+str(path))
        protocol=json.loads((path/'protocol.json').read_text())
        if protocol.get('stage')!='formal':raise ValueError('pilot/profile records cannot enter the formal corpus')
        loaded=read_records(path/'episodes.jsonl')
        if len(loaded)!=summary['attempted']:raise ValueError('campaign summary and recorded denominator differ')
        for raw in loaded:
            row=copy.deepcopy(raw);identity=row['episode_id']
            row['episode_id']=entry['id']+'/'+identity
            row['artifact_directory']=str((path/identity).resolve())
            row.setdefault('operation_policy',protocol['policy'])
            validate_record(row);rows.append(row)
        sources.append({'id':entry['id'],'path':str(path),'summary':summary})
    if len({r['episode_id'] for r in rows})!=len(rows):raise ValueError('duplicate episode identity')
    split=spec['split'];validate_split(rows,split)
    with (out/'episodes.jsonl').open('x') as stream:
        for row in rows:stream.write(json.dumps(row,allow_nan=False)+'\n')
    with (out/'unseen_composition_episodes.jsonl').open('x') as stream:
        withheld=0
        for row in rows:
            length=next((len(p['actions']) for p in row.get('input',{}).get('plans',[]) if p['id']==row['chosen_plan']),None)
            if group_key(row) not in split['test'] and length is not None and length>1:
                withheld+=1;continue
            stream.write(json.dumps(row,allow_nan=False)+'\n')
    (out/'split.json').write_text(json.dumps(split,indent=2)+'\n')
    for application in sorted({r['application'] for r in rows}):
        test=[g for partition in split.values() for g in partition if g.startswith(application+'/')]
        validation=[g for g in split['validation'] if g not in test]
        train=[g for partition in split.values() for g in partition if g not in test+validation]
        cross={'train':train,'validation':validation,'test':test};validate_split(rows,cross)
        (out/('leave_'+application+'_split.json')).write_text(json.dumps(cross,indent=2)+'\n')
    topology_test=[g for values in split.values() for g in values if '/F3-' in g]
    topology_validation=[g for g in split['validation'] if g not in topology_test]
    topology_train=[g for values in split.values() for g in values if g not in topology_test+topology_validation]
    topology={'train':topology_train,'validation':topology_validation,'test':topology_test}
    validate_split(rows,topology)
    (out/'unseen_topology_split.json').write_text(json.dumps(topology,indent=2)+'\n')
    (out/'summary.json').write_text(json.dumps({'status':'complete','source':'live','attempts':len(rows),
        'input_episodes':sum('input' in r for r in rows),'independent_groups':len({group_key(r) for r in rows}),
        'unseen_composition_training_validation_rows_withheld':withheld,'campaigns':sources,
        'scope':'original attempts preserved, no effect imputation, no pilot/profile data'},indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--manifest',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();pack(a.manifest,a.out)
