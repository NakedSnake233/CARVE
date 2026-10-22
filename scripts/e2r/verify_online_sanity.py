#!/usr/bin/env python3
"""Check included laboratory strategy records and resources before remaining jobs."""
import argparse
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.data import validate_record
from methods.E2R.intervention.platform import KubectlClient
from methods.E2R.intervention.schema import Plan


def verify_registered_plan(plan, catalog, chosen):
    registered=next((p for p in catalog if p['id']==chosen),None)
    if plan is None or registered is None or plan.to_dict()!=Plan.from_dict(registered).to_dict():
        raise ValueError('unregistered selected action')


def verify(queue, out=None):
    queue=Path(queue);registration=json.loads((queue/'PREREGISTRATION.json').read_text())
    checks=[];peak=[];children=[];observed_apps=set();executed=[]
    for assignment in registration['assignments']:
        if assignment['id'] not in registration['first_included_jobs']:continue
        folder=Path(assignment['out']);config=json.loads((folder/'protocol.json').read_text())
        summary=json.loads((folder/'summary.json').read_text());restore=summary['campaign_baseline_restore']
        if summary['status']!='complete' or summary['attempted']!=4:raise ValueError('included source incomplete')
        if restore['status']!='restored' or not restore.get('native_canaries') or not all(restore['native_canaries'].values()):raise ValueError('unverified source restoration')
        peak.append(restore['laboratory_process_peak_rss_mib']);children.append(restore['maximum_child_rss_mib'])
        rows=[json.loads(line) for line in (folder/'episodes.jsonl').read_text().splitlines()]
        if len(rows)!=4:raise ValueError('attempt ledger incomplete')
        for row,job in zip(rows,config['scheduled_jobs']):
            _,plan=validate_record(row)
            if row['assigned_method']!=job['arm'] or row['original_schedule_slot']!=job['original_schedule_slot']:raise ValueError('strategy/slot mismatch')
            if json.loads((folder/row['episode_id']/'episode.json').read_text())!=row or row['restore']['status']!='restored':raise ValueError('raw mismatch or restore failure')
            if 'input' not in row:continue # e.g. actual non-degrading injection, retained denominator
            verify_registered_plan(plan,config['plans'],row['chosen_plan'])
            decision=row.get('decision',{})
            if decision.get('validation_profile_status')!='not_qualified_laboratory_experiment':raise ValueError('laboratory qualifier missing')
            if any(c['strict_admission_diagnostic']['qualified_profile'] is not False for c in decision['candidates']):raise ValueError('experimental execution misclassified as qualified')
            budget=row['total_probe_budget']
            if budget['limits']!=config['total_probe_budget'] or not budget['decision_boundary']:raise ValueError('missing budget scope')
            if budget['calls']>budget['limits']['max_calls'] or budget['cost']>budget['limits']['max_cost']:raise ValueError('total probe budget exceeded')
            ledger=folder/row['episode_id']/'registered_probe_events.jsonl';latest={}
            for line in ledger.read_text().splitlines() if ledger.exists() else []:
                e=json.loads(line);latest[e['sequence']]=e
            if [latest[k] for k in sorted(latest)]!=budget['events']:raise ValueError('query journal mismatch')
            if decision['budget']['calls']>3 or decision['budget']['cost']>6:raise ValueError('active query budget exceeded')
            if row['status']=='observed':
                observed_apps.add(row['application'])
                if not row['outcome']['verification'].get('during_strategy_non_target'):raise ValueError('query safety interval absent')
            if row['execution']['status']=='executed':
                if not all(s.get('write_verified') and s.get('controller_converged') for s in row['execution']['steps']):raise ValueError('write path unverified')
                if not budget['pre_write_authorization_checks'] or not all(a['approved'] and a['elapsed_seconds']<=budget['limits']['max_seconds'] for a in budget['pre_write_authorization_checks']):raise ValueError('write lacks in-budget authorization')
                executed.append({'application':row['application'],'strategy':row['assigned_method'],'plan':row['chosen_plan']})
        checks.append({'id':assignment['id'],'namespace':assignment['namespace'],'attempts':4})
    if len(checks)!=8 or len({c['namespace'] for c in checks})!=8:raise ValueError('sanity must cover all8 environments')
    if observed_apps!={a['application'] for a in registration['assignments']}:raise ValueError('no observed strategy outcomes for one application')
    release=json.loads((queue/'RELEASE.json').read_text())
    prior_path=Path(release['prior_shared_executor_sanity'])
    prior=json.loads(prior_path.read_text())
    if prior.get('status')!='passed':raise ValueError('shared executor engineering witness absent')
    for ns in sorted({c['namespace'] for c in checks}):
        client=KubectlClient(ns)
        for d in client.list('Deployment'):
            desired=d['spec'].get('replicas',1);status=d.get('status',{})
            if status.get('readyReplicas',0)!=desired or status.get('updatedReplicas',0)!=desired:raise ValueError('deployment not restored/ready')
    mem=next(int(line.split()[1])/1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
    # Measured parent and maximum child RSS are conservative per-lane estimates;
    # native Pods are already in host use. Keep4GiB for unmeasured contention.
    required=8*(max(peak)+max(children))+4096
    if mem<required:raise ValueError('eight-lane memory headroom insufficient; preserve sanity and reduce queue concurrency')
    report={'status':'passed','checked_at':time.time(),'included_attempts':32,'checks':checks,'executed_paths':executed,
        'source_queue':str(queue.resolve()),'verifier_runtime':str(ROOT),
        'prior_shared_executor_sanity':str(prior_path),'new_callback_tests':release['laboratory_callback_tests'],
        'write_coverage_note':'Current actual selected writes listed above; empty does not become positive coverage. Prior shared native executor and new laboratory callback engineering checks are separate evidence.',
        'peak_parent_rss_mib':max(peak),'maximum_child_rss_mib':max(children),'available_memory_mib':mem,
        'required_headroom_mib':required,'released_max_parallel':8,
        'scope':'Engineering integrity, budget, observed safety availability, restoration and resources. Recovery/safety false remains data; no positive coverage or scientific qualification prerequisite.'}
    destination=Path(out) if out is not None else queue/'SANITY_GATE.json'
    with destination.open('x') as stream:stream.write(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--queue',required=True);p.add_argument('--out');a=p.parse_args();verify(a.queue,a.out)
