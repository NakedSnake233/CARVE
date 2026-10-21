#!/usr/bin/env python3
"""Host-side model queue checks and same-thread Codex recovery notifications."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import time
import uuid
import subprocess
import sys

from supervise_native_collection import read, save, process_alive, CodexRPC, dispatch


def inspect(config, now):
    issues, jobs, queues = [], [], []
    for declaration in config['queues']:
        folder=Path(declaration['directory']);selected=declaration.get('job_ids')
        if not (folder/'queue_state.json').exists():
            issues.append(str(folder)+':queue_state_missing');continue
        state=read(folder/'queue_state.json');manifest=read(folder/'manifest.json')
        declarations={j['id']:j for j in manifest['jobs']}
        selected=set(selected or state['jobs'])
        unfinished=False
        for name in sorted(selected):
            row=dict(state['jobs'][name]);job_folder=folder/'jobs'/name
            if (job_folder/'receipt.json').exists():row.update(read(job_folder/'receipt.json'))
            status=row['status'];item={'queue':str(folder),'id':name,'status':status}
            unfinished|=status!='completed'
            if status in ('stuck','blocked_dependency','blocked_resource','blocked_budget'):
                issues.append(folder.name+'/'+name+':'+status)
            if status in ('running','launching'):
                if (job_folder/'started.json').exists():
                    started=read(job_folder/'started.json');live=process_alive(started['pid'],started['start_ticks'])
                    item.update(worker_pid=started['pid'],worker_alive=live)
                    if not live and not (job_folder/'receipt.json').exists():issues.append(folder.name+'/'+name+':worker_lost')
                elif now-row.get('launch_at',now)>30:issues.append(folder.name+'/'+name+':worker_unregistered')
                outputs=declarations[name].get('outputs',[])
                progress_path=Path(outputs[0]['path']).parent/'progress.json' if outputs else None
                if progress_path and progress_path.exists():
                    progress=read(progress_path);item['progress']=progress
                    if now-progress.get('updated_at',now)>config.get('stall_seconds',1200):
                        issues.append(folder.name+'/'+name+':progress_stale')
            jobs.append(item)
        if unfinished and now-state.get('updated_at',0)>180:issues.append(folder.name+':scheduler_heartbeat_stale')
        queues.append({'directory':str(folder),'reported_status':state.get('status'),
                       'selected_jobs':len(selected),'unfinished':unfinished})
    counts={status:sum(j['status']==status for j in jobs) for status in sorted({j['status'] for j in jobs})}
    return {'checked_at':now,'issues':sorted(set(issues)),'jobs':jobs,'queues':queues,'counts':counts,
            'complete':bool(jobs) and len(queues)==len(config['queues']) and all(j['status']=='completed' for j in jobs)
                       and not issues,
            'load_average':list(os.getloadavg()),'queue_signature':json.dumps(config['queues'],sort_keys=True)}


def update_cost_report(config,report):
    if config.get('cost_reporter'):
        try:
            completed=subprocess.run([sys.executable,config['cost_reporter'],'--root',config['cost_study_root']],capture_output=True,text=True,timeout=45,check=True)
            report['cost_accounting']=json.loads(completed.stdout)
        except Exception as error:
            report['issues'].append('cost_accounting_error:'+type(error).__name__)
            report['complete']=False


def tick(config):
    home=Path(config['directory']);state_path=home/'state.json';now=time.time()
    if not config.get('enabled',True):print('disabled');return
    state=read(state_path) if state_path.exists() else {}
    try:report=inspect(config,now)
    except Exception as error:report={'checked_at':now,'issues':['inspection_error:'+str(error)],'complete':False,'counts':{},'queue_signature':json.dumps(config['queues'],sort_keys=True)}
    update_cost_report(config,report)
    save(home/'LATEST.json',report);snapshot=home/'snapshots'/f'{int(now)}.json';save(snapshot,report)
    request=state.get('request')
    if request:
        ack=home/'acks'/(request['id']+'.json')
        if ack.exists():
            value=read(ack)
            if value.get('request_id')!=request['id'] or value.get('status') not in ('completed','needs_attention'):
                raise ValueError('invalid learning supervision ack')
            state['last_ack']=value
            if value['status']=='needs_attention':
                if request['kind']=='queue_complete':state['completion_attention_for']=request['queue_signature']
                else:state['attention_issues']=request['issues']
            elif request['kind']=='queue_complete':state['completion_reported_for']=request['queue_signature']
            state['request']=None;request=None
        elif request['phase']=='prepared' and (request['queue_signature']!=report['queue_signature'] or
                request['kind']=='repair' and not report['issues'] or request['kind']=='queue_complete' and not report['complete']):
            state['retired_prepared_request']={**request,'retired_at':now,'reason':'stage_changed_or_resolved_before_dispatch'}
            state['request']=None;request=None
    if not report['issues']:state.pop('attention_issues',None)
    kind='repair' if report['issues'] else 'queue_complete' if (report['complete']
        and state.get('completion_reported_for')!=report['queue_signature']
        and state.get('completion_attention_for')!=report['queue_signature']) else None
    if not request and kind and not (kind=='repair' and state.get('attention_issues')==report['issues']):
        request={'id':str(uuid.uuid4()),'kind':kind,'phase':'prepared','created_at':now,
                 'issues':report['issues'],'queue_signature':report['queue_signature']}
        request['prompt']=(f'【完整实验自动巡检：{kind}】用户已授权补齐并运行完整实验、停止则修复继续。\n'
            f'项目{config["project"]}。请读取{config["handoff"]}、{config["plan"]}和{home / "SUPERVISION.md"}。\n'
            f'请求{request["id"]}；快照{snapshot}；最新{home / "LATEST.json"}。重新核实实际进程/进度。\n'
            '修复当前失败并继续未完成作业，保留全部日志和失败/旧checkpoint；不覆盖有效结果、不根据测试评分调参。'
            '队列完成时继续剩余训练/评估/审查，不把单个阶段当全项目完成。沿用沙箱和自动审批，不新增外部资源或第三方消息。\n'
            f'完成本次处理后原子写{home / "acks" / (request["id"]+".json")}，'
            '包含request_id、status(completed或needs_attention)、summary、checked_at。只有真正需用户输入才用needs_attention。')
        state['request']=request;save(home/'requests'/(request['id']+'.json'),request)
    def persist():save(state_path,state)
    persist();outcome='complete' if report['complete'] else 'healthy' if not report['issues'] else 'needs_repair'
    if request:
        rpc=None
        try:
            rpc=CodexRPC(config['codex_socket']);outcome=dispatch(rpc,config,state,persist,now)
            if outcome=='awaiting_ack_after_delivery' and now-request.get('sent_at',now)>600:
                state['last_unacknowledged_request']=request;state['request']=None
        except Exception as error:outcome='dispatch_error:'+str(error)
        finally:
            if rpc:rpc.close()
    state.update(last_check_at=now,last_outcome=outcome);persist()
    save(home/'LAST_CHECK.json',{'checked_at':now,'outcome':outcome,'issues':report['issues'],'counts':report['counts']})
    print(json.dumps({'outcome':outcome,'issues':report['issues'],'counts':report['counts']}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True,type=Path);a=p.parse_args();c=read(a.config)
    home=Path(c['directory']);home.mkdir(parents=True,exist_ok=True)
    with(home/'watch.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('previous check still running')
        tick(c)
