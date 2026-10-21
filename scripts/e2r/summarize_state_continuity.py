#!/usr/bin/env python3
"""Validate acknowledged-state evidence and report every registered native arm."""
import argparse,json,sys,time
from collections import Counter,defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.data import validate_record
from methods.E2R.intervention.effect_pairs import repeated_effect_pairs
from scripts.e2r.pack_revision_native import pack
from scripts.e2r.terminal_recovery import validate_terminal_recovery

def read(p):return json.loads(Path(p).read_text())
def save(p,v):Path(p).write_text(json.dumps(v,indent=2,allow_nan=False)+'\n')

def check_source(folder,require_observed=False):
    folder=Path(folder);config=read(folder/'protocol.json');summary=read(folder/'summary.json')
    restore=summary['campaign_baseline_restore'];recovery=None
    if summary['status']!='complete' or restore['status']!='restored' or not all(restore['native_canaries'].values()):
        recovery=validate_terminal_recovery(folder,config,summary,read(folder/'TERMINAL_RECOVERY.json'))
    rows=[json.loads(l) for l in (folder/'episodes.jsonl').read_text().splitlines() if l.strip()]
    assert len(rows)==summary['attempted'] and len(rows)<=len(config['scheduled_jobs'])
    for row in rows:
        validate_record(row);assert row['restore']['status']=='restored' or row['episode_id'] in (recovery or {}).get('recovered_episode_ids',[])
        if require_observed:assert row['status']=='observed'
        if row['status']!='observed':continue
        episode=folder/row['episode_id'];receipt=read(episode/'acknowledged_state_before_fault.json')
        baseline=read(episode/'baseline_business.json')['window']
        state=config['state_continuity'];required=state.get('required_cart',state.get('required_station'))
        assert receipt['status']=='acknowledged' and receipt['required']==required
        assert receipt['owner_database']==config['state_continuity']['owner_database'] and receipt['completed_at']<baseline['start_time']
        assert row['outcome']['acknowledged_state_goal']['preincident_receipt']==receipt
        assert baseline['state_canary_ok'] is True
        budget=row['total_probe_budget'];assert budget['calls']<=16 and budget['cost']<=48 and not budget['actual_runtime_exceeded']
        assert row['outcome']['verification']['during_strategy_non_target']
    return rows

def run(queue,stage):
    queue=Path(queue);plan=read(queue/'PREREGISTRATION.json')
    sources=[a for a in plan['assignments'] if stage=='final' or a['id'] in plan['included_first_jobs']]
    raw=[]
    source_queues=read(queue/'SOURCE_QUEUES.json')['source_queues'] if (queue/'SOURCE_QUEUES.json').exists() else []
    if stage=='final' and source_queues:
        sources=[]
        for q in [str(queue),*source_queues]:
            for j in read(Path(q)/'manifest.json')['jobs']:
                if j.get('kind')=='gate':continue
                command=j['command'];out=Path(command[command.index('--out')+1])
                if out.exists():sources.append({'out':str(out)})
    for source in sources:raw.extend(check_source(source['out'],require_observed=stage=='sanity'))
    if stage=='sanity':
        save(queue/'SANITY_GATE.json',{'status':'passed','attempts':len(raw),'checked_at':time.time(),
            'scope':'Acknowledgment chronology, raw/schema/budget/restore checks only; no positive recovery requirement.'});return
    pack(queue,source_queues,queue/'data',plan.get('cohort',queue.name))
    rows=[json.loads(l) for l in (queue/'data/episodes.jsonl').read_text().splitlines()]
    groups=defaultdict(lambda:Counter(n=0,recovered=0,safe=0,continuity_service_success=0,unknown=0))
    failures=Counter();continuity=Counter();station_queries=Counter()
    for row in rows:
        cell=groups[(row['condition_id'],row['chosen_plan'])];cell['n']+=1
        effects=row.get('outcome',{}).get('effects',{});cell['recovered']+=effects.get('business_recovered') is True;cell['safe']+=effects.get('non_target_safe') is True
        cell['unknown']+=effects.get('business_recovered') is None
        canaries=row.get('outcome',{}).get('acknowledged_state_goal',{}).get('post_window_state_canaries',[]);cell['continuity_service_success']+=bool(canaries) and all(canaries)
        p=row['source_provenance'];post_path=Path(p['source_directory'])/p['original_episode_id']/'post_business.json'
        if not post_path.exists():continue
        post=read(post_path)
        for window in post['raw']:
            for tx in window['transaction_results']:
                d=tx.get('diagnostics',{});failures[d.get('failure','none')]+=1
                if 'continuity_preserved' in d:continuity[str(d['continuity_preserved']).lower()]+=1
                if row['application'].startswith('trainticket'):
                    observations=d.get('station_goal_observations',[])
                    if not observations:station_queries['unobserved']+=1
                    for observed in observations:station_queries['confirmed' if observed.get('goal_confirmed') is True else 'not_confirmed']+=1
    pairs=repeated_effect_pairs(rows,[r['episode_id'] for r in rows if 'input' in r])
    result={'status':'complete','checked_at':time.time(),'attempts':len(rows),'cells':[{'condition':k[0],'arm':k[1],**v} for k,v in sorted(groups.items())],
        'station_goal_queries':dict(station_queries),'transaction_failure_reasons':dict(failures),'transaction_continuity_checks':dict(continuity),'supported_same_action_effect_pairs':pairs,
        'scope':plan['scope'],'continuity_diagnostics_scope':('Native Station acknowledged-ID response checks; missing observations are unobserved, not evidence of data erasure' if station_queries else 'true/false only for successful GetAcknowledgedCart responses; absent checks are unobserved, not evidence of state loss'),'peer_colocation_caveat':'Isolated standby shares the alternative (non-owner) store with peer; latency/safety effects cannot be attributed exclusively to continuity.','not_confirmatory_test':True,'old_outcomes_unchanged':True}
    save(queue/'RESULTS.json',result)
    lines=['# 已确认业务状态连续性实验','',plan['scope'],'','| 存储条件 | 动作 | n | 完整恢复 | 非目标安全 | 三个窗口末 canary 均通过 |','|---|---|---:|---:|---:|---:|']
    for c in result['cells']:lines.append(f"| {c['condition']} | {c['arm']} | {c['n']} | {c['recovered']} | {c['safe']} | {c['continuity_service_success']} |")
    lines+=['',f'同诊断、同动作、同协议的有区间支持差异对：{len(pairs)}。','本轮加入故障前确认数据必须保留的新业务目标；旧无状态实验不改写。不代表学习模型优势、独立泛化或完整 C1/C2 证明。']
    (queue/'RESULTS.md').write_text('\n'.join(lines)+'\n')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--queue',required=True);p.add_argument('--stage',choices=['sanity','final'],required=True);a=p.parse_args();run(a.queue,a.stage)
