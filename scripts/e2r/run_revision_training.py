#!/usr/bin/env python3
"""Run one predeclared v2 development model, preserving the v1 test exclusion."""
import argparse
import json
from pathlib import Path
import resource
import sys
import time
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.learning_v2 import fit_v2,RelationalPredictor,save
from methods.E2R.intervention.data import group_key,validate_record
from methods.E2R.intervention.study_analysis import prediction_metrics
from methods.E2R.intervention.learning_views import record_for_view


def load_development_records(data,original):
    split={'train':original['train'],'development':original['validation'],'excluded':original['test']}
    allowed=set(split['train']+split['development']);rows=[]
    # Do not invoke label verification or model processing on the excluded old test.
    with Path(data).open() as stream:
        for line in stream:
            row=json.loads(line)
            if group_key(row) in allowed:rows.append(row)
    if len({r['episode_id'] for r in rows})!=len(rows):raise ValueError('duplicate included attempt identity')
    if {r['source'] for r in rows}!={'live'}:raise ValueError('native development requires homogeneous live provenance')
    for row in rows:validate_record(row)
    return rows,split


def run(plan_path,identity):
    plan=json.loads(Path(plan_path).read_text());entry=next(m for m in plan['models'] if m['id']==identity)
    if str(ROOT)!=plan['runtime']:raise ValueError('use frozen revision runtime')
    release=json.loads(Path(plan['review_release']).read_text())
    if release['status']!='passed' or release['scope']!='development_only_no_live_authorization':raise ValueError('required review release missing')
    if identity!='profile_graph_s42':
        gate=json.loads(Path(plan['native_profile_gate']).read_text())
        if gate['status']!='passed' or gate['test_evaluated'] is not False:raise ValueError('native profile not passed')
    started=time.time();original=json.loads(Path(plan['original_split']).read_text())
    rows,split=load_development_records(plan['data'],original)
    result=fit_v2(rows,split,entry['out'],run_metadata={'plan':str(Path(plan_path).resolve()),'id':identity,'scope':plan['scope']},**entry['parameters'])
    predictor=RelationalPredictor(entry['out'],seeds=entry['parameters']['seeds'])
    development=[record_for_view(r,predictor.metadata.get('input_view','full')) for r in rows if group_key(r) in split['development']]
    metrics=prediction_metrics(development,predictor)
    metrics['input_view']=predictor.metadata.get('input_view','full')
    metrics['distinguishability']['scope']='legacy v1 diagnostic only; not the v2 same-diagnosis business/safety pair evaluator or C1 confirmation'
    save(Path(entry['out'])/'development_predictions.json',metrics)
    report={'status':'complete','id':identity,'runtime':str(ROOT),'test_evaluated':False,'admission_status':'uncalibrated_no_live_authorization',
        'wall_seconds':time.time()-started,'peak_rss_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,'completed_at':time.time(),
        'development_business_brier':metrics['effects']['business_recovered']['brier'],'training_summary':result['seeds']}
    save(Path(entry['out'])/'DEVELOPMENT_DONE.json',report);print(json.dumps(report),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',required=True);p.add_argument('--id',required=True);a=p.parse_args();run(a.plan,a.id)
