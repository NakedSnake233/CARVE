#!/usr/bin/env python3
"""Read-only verified restoration after a terminal canary failure."""
import argparse
import fcntl
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from scripts.e2r.terminal_recovery import baseline_fields,canary_specs,validate_terminal_recovery
from methods.E2R.intervention.platform import KubectlClient
from methods.E2R.intervention.contracts import field_value
from methods.E2R.intervention.campaign import http_request


def verify(directory,post_outcome_cleanup=False):
    directory=Path(directory).resolve();config=json.loads((directory/'protocol.json').read_text());summary=json.loads((directory/'summary.json').read_text())
    namespace=config['experimental_namespace']
    with (Path(config['resource_lock_directory'])/(namespace+'.lock')).open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        client=KubectlClient(namespace,config.get('kubeconfig'))
        labels=client._call(['get','namespace',namespace,'-o','json'])['metadata'].get('labels',{})
        if labels.get('e2r-owner')!='e2r-intervention-native-20260929':raise ValueError('unowned namespace')
        checks=[]
        for expected in baseline_fields(directory,config):
            obj=client.get(expected['kind'],expected['name']);current=field_value(obj,expected['path'])
            matched=current==expected['expected'] and obj['metadata']['uid']==expected['uid']
            checks.append({**expected,'actual':current,'observed_uid':obj['metadata']['uid'],'matched':matched})
            if not matched:raise ValueError('original baseline not restored; no write attempted')
        controllers=[]
        for obj in client.list('Deployment'):
            desired=obj['spec'].get('replicas',1);status=obj.get('status',{})
            ready=(status.get('readyReplicas',0)==desired and status.get('updatedReplicas',0)==desired
                   and status.get('observedGeneration',0)>=obj['metadata'].get('generation',1))
            controllers.append({'name':obj['metadata']['name'],'uid':obj['metadata']['uid'],'desired':desired,'ready':ready})
            if not ready:raise ValueError('controller not ready')
        if any(p['metadata'].get('deletionTimestamp') for p in client.list('Pod')):raise ValueError('terminating Pods remain')
        rounds=[];specs=canary_specs(config)
        for index in range(3):
            if index:time.sleep(2)
            row={'started_at':time.time(),'checks':{}}
            for name,spec in specs.items():
                detail={};ok,latency=http_request(spec,detail=detail)
                row['checks'][name]={'success':ok,'latency_ms':latency,'detail':detail}
            row['completed_at']=time.time();rounds.append(row)
            if not all(v['success'] for v in row['checks'].values()):raise ValueError('canary failed; recovery not released')
        receipt={'schema':'native_terminal_health_recovery_v1','status':'verified','source_directory':str(directory),
                 'source_summary':summary,'namespace':namespace,'namespace_lock_held':True,'mutations_performed':False,
                 'baseline_checks':checks,'deployment_checks':controllers,'canary_specs':specs,'canary_rounds':rounds,
                 'verified_at':time.time(),'scope':'Subsequent environment recovery evidence only. Prior final-canary failure and all episode labels remain unchanged; no experiment repeated.'}
        if post_outcome_cleanup:
            rows=[json.loads(l) for l in (directory/'episodes.jsonl').read_text().splitlines()]
            receipt.update(scope_kind='post_outcome_cleanup',recovered_episode_ids=[r['episode_id'] for r in rows if r['restore']['status']=='failed'],scope='Subsequent environment health only; observed outcomes and original per-episode cleanup failure unchanged; no observed slot repeated.')
        validate_terminal_recovery(directory,config,summary,receipt)
        with (directory/'TERMINAL_RECOVERY.json').open('x') as stream:stream.write(json.dumps(receipt,indent=2)+'\n')
        print(json.dumps({'status':'verified','namespace':namespace,'baseline_fields':len(checks),'canary_rounds':3,'canaries_per_round':len(specs)}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',required=True);p.add_argument('--post-outcome-cleanup',action='store_true');a=p.parse_args();verify(a.directory,a.post_outcome_cleanup)
