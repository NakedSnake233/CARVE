#!/usr/bin/env python3
"""Validate included native execution/data/restoration without a scientific pass gate."""
import argparse
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.data import validate_record
from methods.E2R.intervention.schema import Plan


def verify(queue):
    queue=Path(queue);registration=json.loads((queue/'PREREGISTRATION.json').read_text());checks=[];executed=set()
    for assignment in registration['assignments']:
        if assignment['id'] not in registration['first_included_jobs']:continue
        folder=Path(assignment['out']);summary=json.loads((folder/'summary.json').read_text());config=json.loads((folder/'protocol.json').read_text())
        if summary['status']!='complete' or summary['attempted']!=3:raise ValueError('included source incomplete')
        restore=summary['campaign_baseline_restore']
        if restore['status']!='restored' or not restore.get('native_canaries') or not all(restore['native_canaries'].values()):raise ValueError('source final restore unverified')
        rows=[json.loads(l) for l in (folder/'episodes.jsonl').read_text().splitlines()]
        if len(rows)!=3:raise ValueError('included attempt ledger differs from summary')
        catalog={p['id']:Plan.from_dict(p) for p in config['plans']}
        for index,row in enumerate(rows):
            _,plan=validate_record(row)
            if plan is not None and plan.to_dict()!=catalog[row['chosen_plan']].to_dict():raise ValueError('recorded action differs from assignment catalog')
            if row.get('original_schedule_slot')!=config['scheduled_jobs'][index]['original_schedule_slot']:raise ValueError('slot provenance differs')
            episode=folder/row['episode_id']
            if json.loads((episode/'episode.json').read_text())!=row:raise ValueError('raw attempt differs from ledger')
            if row.get('assigned_method')!='experiment_assigned_fixed_observation':raise ValueError('missing explicit assignment marker')
            if row['restore']['status']!='restored':raise ValueError('attempt restore failed')
            budget=row.get('total_probe_budget',{})
            if (budget.get('limits')!=config['total_probe_budget'] or budget.get('actual_runtime_exceeded')
                    or not budget.get('decision_boundary')):raise ValueError('missing/invalid budget boundary')
            if budget['calls']>budget['limits']['max_calls'] or budget['cost']>budget['limits']['max_cost']:raise ValueError('budget exceeded')
            latest={}
            for line in (episode/'registered_probe_events.jsonl').read_text().splitlines():
                event=json.loads(line);latest[event['sequence']]=event
            if [latest[k] for k in sorted(latest)]!=budget['events']:raise ValueError('query journal and summary differ')
            if row['status']=='observed' and not row['outcome']['verification'].get('during_strategy_non_target'):
                raise ValueError('initial query safety measurement absent')
            if row['execution']['status']=='executed':
                steps=row['execution']['steps']
                if (len(steps)!=len(plan.actions) or any(s.get('operation')!=a.operation or s.get('write_verified') is not True
                        or s.get('controller_converged') is not True for s,a in zip(steps,plan.actions))):raise ValueError('native executed path unverified')
                executed.add((row['application'],row['chosen_plan']))
        checks.append({'job':assignment['id'],'rows':len(rows),'namespace':assignment['namespace'],'restored':True})
    apps={a['application'] for a in registration['assignments']}
    if len(checks)!=8 or len({r['namespace'] for r in checks})!=8:raise ValueError('included sanity does not cover all8 environments')
    if any((app,arm) not in executed for app in apps for arm in ('switch_standby','rollback_active')):
        raise ValueError('native write path not exercised in both applications')
    report={'status':'passed','checked_at':time.time(),'scope':'Actual execution, record integrity, budget, query-safety availability and restoration only; scientific recovery/safety labels do not gate exploratory collection.',
            'included_attempts':24,'checks':checks,'executed_paths':sorted(executed)}
    with (queue/'SANITY_GATE.json').open('x') as stream:stream.write(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'status':'passed','included_attempts':24}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--queue',required=True,type=Path);a=p.parse_args();verify(a.queue)
