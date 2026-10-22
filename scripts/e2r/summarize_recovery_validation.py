#!/usr/bin/env python3
"""Common-reference validation with explicit failed and unstarted accounting."""
import argparse,json,sys,math,time
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.data import validate_record
from scripts.e2r.summarize_state_continuity import run as summarize
from scripts.e2r.summarize_startup_study import cpu_window

def read(p):return json.loads(Path(p).read_text())
def save(p,v):Path(p).write_text(json.dumps(v,indent=2,allow_nan=False)+'\n')

def terminal_accounting(q,plan):
    positions=[];attempts=[];complete=True
    for a in plan['assignments']:
        config=read(a['config']);folder=Path(a['out']);scheduled=config['scheduled_jobs']
        if not folder.exists():
            complete=False;ledger=[]
        else:
            if not (folder/'summary.json').exists():raise RuntimeError('source still running; wait before final accounting')
            summary=read(folder/'summary.json');assert summary['status'] in ('complete','stopped')
            assert read(folder/'protocol.json')==config
            ledger=[json.loads(l) for l in (folder/'episodes.jsonl').read_text().splitlines() if l.strip()]
            assert len(ledger)==summary['attempted'] and len(ledger)<=len(scheduled)
            complete &= summary['status']=='complete' and summary['campaign_baseline_restore']['status']=='restored' and bool(summary['campaign_baseline_restore'].get('native_canaries')) and all(summary['campaign_baseline_restore']['native_canaries'].values())
        for index,job in enumerate(scheduled):
            position={'source_id':a['id'],'source_directory':str(folder),'original_schedule_slot':job['original_schedule_slot'],'group_id':job['group_id'],'condition_id':job['condition_id'],'arm':job['arm'],'repeat':job['repeat']}
            if index>=len(ledger):position.update(status='unstarted',attempt_id=None);complete=False
            else:
                row=ledger[index];validate_record(row)
                assert read(folder/row['episode_id']/'episode.json')==row
                for key in ('original_schedule_slot','group_id','condition_id','repeat'):assert row[key]==job[key]
                assert row['chosen_plan']==job['arm']
                identity=q.name+'/'+a['id']+'/'+row['episode_id']
                position.update(status=row['status'],attempt_id=identity,restore_status=row['restore']['status'])
                attempts.append({'attempt_id':identity,'source_directory':str(folder),'raw_episode':str(folder/row['episode_id']/'episode.json'),'raw':row})
                complete &= row['status'] not in ('failed','started') and row['restore']['status']=='restored'
            positions.append(position)
    assert len(positions)==plan['planned_attempts']
    assert len({(p['group_id'],p['original_schedule_slot']) for p in positions})==len(positions)
    accounting={'status':'complete' if complete else 'partial','created_at':time.time(),'planned_positions':len(positions),'started_attempts':len(attempts),
        'status_counts':dict(Counter(p['status'] for p in positions)),'unknown_business_attempts':sum(a['raw'].get('outcome',{}).get('effects',{}).get('business_recovered') is None for a in attempts),
        'positions':positions,'scope':'Unstarted slots are not attempts; failed/unknown attempts remain in denominators. Native stop/restore gates are not bypassed.'}
    save(q/'ATTEMPT_ACCOUNTING.json',accounting)
    with (q/'ALL_STARTED_ATTEMPTS.jsonl').open('w') as stream:
        for row in attempts:stream.write(json.dumps(row,allow_nan=False)+'\n')
    return accounting

def run(q,stage):
    q=Path(q);plan=read(q/'PREREGISTRATION.json')
    if stage=='final':
        accounting=terminal_accounting(q,plan)
        if accounting['status']!='complete':
            save(q/'RESULTS.json',{'status':'partial','accounting':accounting,'scope':plan['scope'],'reason':'Native collection halted; retain failed and unstarted positions without replacement. Full training pack not produced.'})
            (q/'RESULTS.md').write_text('# 采集停止：保留完整分母\n\n'+f"计划{accounting['planned_positions']}位置；已启动{accounting['started_attempts']}次。状态{accounting['status_counts']}；业务结果unknown的已启动尝试{accounting['unknown_business_attempts']}。\n\n失败/未启动均显式报告，未启动不计作尝试，不为通过打包门而替换失败。全部原始行见ALL_STARTED_ATTEMPTS.jsonl；来源不改写。\n")
            return
    diagnostics=[]
    for a in plan['assignments']:
        if stage=='sanity' and a['id'] not in plan['included_first_jobs']:continue
        folder=Path(a['out']);config=read(folder/'protocol.json');ceiling=config['baseline_latency_ceiling_ms']
        for line in (folder/'episodes.jsonl').read_text().splitlines():
            row=json.loads(line)
            if row['status']!='observed':continue
            o=row['outcome'];p=o['verification_protocol'];thresholds={}
            for name,w in o['baseline_windows'].items():
                assert w['p95_ms']<=ceiling
                thresholds[name]=max(w['p95_ms']*p['latency_multiplier'],p['latency_floor_ms'])
                assert math.isclose(thresholds[name],p['latency_floor_ms'],abs_tol=1e-9)
            episode=folder/row['episode_id'];assert read(episode/'STARTUP_DIAGNOSTICS.json')['status']=='complete'
            samples=[json.loads(l) for l in (episode/'startup_cgroup_samples.jsonl').read_text().splitlines()]
            windows=o['verification']['business']['raw_windows'];role='cart-old' if row['chosen_plan']=='switch_standby' else 'cart-new'
            diagnostics.append({'source_id':a['id'],'episode_id':row['episode_id'],'condition':row['condition_id'],'arm':row['chosen_plan'],
                'baseline_p95_ms':{n:w['p95_ms'] for n,w in o['baseline_windows'].items()},'effective_thresholds_ms':thresholds,
                'post_p95_ms':[w['p95_ms'] for w in windows],'cpu_post_windows':[cpu_window(samples,w['start_time'],w['end_time'],role) for w in windows],
                'execution_timings_seconds':row.get('execution',{}).get('timings_seconds',{}),'phase_timings_seconds':row.get('phase_timings_seconds',{}),
                'effects':o['effects'],'raw_episode_directory':str(episode)})
    summarize(q,stage)
    if stage=='final':
        result=read(q/'RESULTS.json');result.update(attempt_accounting=accounting,per_attempt_diagnostics=diagnostics);save(q/'RESULTS.json',result)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--queue',required=True);p.add_argument('--stage',choices=['sanity','final'],required=True);a=p.parse_args();run(a.queue,a.stage)
