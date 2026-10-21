#!/usr/bin/env python3
"""One predeclared native model/seed; no test predictions or model selection here."""
import argparse
import hashlib
import json
import resource
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.data import read_records
from methods.E2R.intervention.learning import fit


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def run(plan_path, identity):
    plan_path=Path(plan_path).resolve();plan=json.loads(plan_path.read_text())
    plan_hash=digest(plan_path)
    if plan_hash!=plan_path.with_suffix('.sha256').read_text().strip():
        raise ValueError('frozen plan differs')
    entry=next(m for m in plan['models'] if m['id']==identity)
    if digest(entry['data'])!=entry['data_sha256'] or digest(entry['split'])!=entry['split_sha256']:
        raise ValueError('frozen data/split differs')
    gate=json.loads(Path(plan['training_sanity_gate']).read_text())
    if gate['status']!='passed' or gate.get('test_evaluated') is not False:
        raise ValueError('training sanity gate not established')
    if str(ROOT)!=plan['training_runtime']:
        raise ValueError('training must use its declared frozen runtime')
    started=time.time()
    rows=read_records(entry['data']);split=json.loads(Path(entry['split']).read_text())
    result=fit(rows,split,entry['out'],seeds=[entry['seed']],run_metadata={
        'model_id':identity,'view':entry['view'],'name':entry['name'],'plan':str(plan_path),'plan_sha256':plan_hash,
        'data_sha256':entry['data_sha256'],'split_sha256':entry['split_sha256'],
        'runtime':str(ROOT),'test_evaluated':False},**entry['parameters'])
    output=Path(entry['out']);progress=json.loads((output/'progress.json').read_text())
    if progress['status']!='complete' or result['training']['seeds']!=[entry['seed']]:
        raise ValueError('training did not complete declared seed')
    checkpoint=output/f'seed{entry["seed"]}.pt'
    if not checkpoint.exists():raise ValueError('missing checkpoint')
    report={'status':'complete','model_id':identity,'seed':entry['seed'],'view':entry['view'],'name':entry['name'],
        'plan_sha256':plan_hash,'data_sha256':entry['data_sha256'],'split_sha256':entry['split_sha256'],
        'runtime':str(ROOT),'checkpoint':str(checkpoint),'checkpoint_sha256':digest(checkpoint),
        'wall_seconds':time.time()-started,'peak_rss_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
        'completed_at':time.time(),'test_evaluated':False,'summary':result['seeds']}
    with(output/'TRAINING_DONE.json').open('x') as stream:json.dump(report,stream,indent=2,allow_nan=False);stream.write('\n')
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',required=True,type=Path);p.add_argument('--model-id',required=True)
    a=p.parse_args();run(a.plan,a.model_id)
