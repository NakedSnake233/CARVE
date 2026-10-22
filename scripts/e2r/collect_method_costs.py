#!/usr/bin/env python3
"""Read-only cost accounting from closed attempts; never infer unknown usage as zero."""
import argparse,csv,io,json,math,statistics,time
from collections import defaultdict,Counter
from pathlib import Path

PHASES=('setup_and_fault_validation','pre_action_observation','refinement_and_decision','execution_and_controller','independent_verification','environment_restore','total_attempt')
TOKEN_KEYS=('input_tokens','output_tokens','total_tokens','cached_input_tokens','reasoning_output_tokens')
LOCAL_METHODS={'goal_reuse','goal_fresh','expert_fresh','blind_fresh'}

def number(v):return v if type(v) in (int,float) and math.isfinite(v) and v>=0 else None

def token_usage(usage):
    usage=usage if isinstance(usage,dict) else {}
    result={'input_tokens':number(usage.get('input_tokens',usage.get('prompt_tokens'))),'output_tokens':number(usage.get('output_tokens',usage.get('completion_tokens'))),'total_tokens':number(usage.get('total_tokens'))}
    for key,fields,subkey in [('cached_input_tokens',('input_tokens_details','prompt_tokens_details'),'cached_tokens'),('reasoning_output_tokens',('output_tokens_details','completion_tokens_details'),'reasoning_tokens')]:
        details=next((usage[k] for k in fields if isinstance(usage.get(k),dict)),{});result[key]=number(details.get(subkey))
    if result['total_tokens'] is None and result['input_tokens'] is not None and result['output_tokens'] is not None:result['total_tokens']=result['input_tokens']+result['output_tokens']
    return result

def stats(values):
    known=sorted(v for v in values if number(v) is not None)
    return {'known_n':len(known),'missing_n':len(values)-len(known),'sum':sum(known) if known else None,'mean':statistics.mean(known) if known else None,'median':statistics.median(known) if known else None,'p95':known[math.ceil(.95*len(known))-1] if known else None}

def save(path,value):
    path=Path(path);tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n');tmp.replace(path)

def native_record(folder,row,index,config,cohort):
    assigned=row.get('assigned_method') or config.get('scheduled_jobs',[{}])[index].get('arm',row.get('chosen_plan'))
    goal=config.get('mode')=='goal_policy_comparison' and assigned in LOCAL_METHODS
    fixed=config.get('mode')=='fixed_observation_evidence'
    if fixed:assigned=config.get('scheduled_jobs',[{}])[index].get('arm',row.get('chosen_plan'))
    local=(goal or fixed) and not any(config.get(k) for k in ('llm','llm_config','llm_model'))
    method=assigned if goal else 'assigned:'+str(assigned) if fixed else str(assigned)
    directory=folder/row['episode_id'];diag=row.get('execution',{}).get('goal_policy_diagnostics',{});timings=row.get('timings_seconds',{})
    api_path=directory/'goal_policy_api_events.json'
    if not diag and api_path.exists():
        events=json.loads(api_path.read_text());diag={'api_reads':sum(e['verb']=='get' for e in events),'api_seconds':sum(e['seconds'] for e in events)}
    prepared=None;journal=directory/'execution_private.jsonl'
    if journal.exists():
        prepared=next((e['time'] for e in (json.loads(l) for l in journal.read_text().splitlines()) if e['event']=='prepared'),None)
    prewrite=prepared-row['action_started_at'] if prepared is not None and 'action_started_at' in row else None
    decision=(timings['pre_action_observation']+timings.get('refinement_and_decision',0)+prewrite) if prewrite is not None and 'pre_action_observation' in timings else None
    return {'cohort':cohort,'method':method,'collector_identity':row.get('assigned_method'),'attempt_id':str(directory.resolve()),'original_slot':row.get('original_schedule_slot'),'status':row['status'],'restore_status':row.get('restore',{}).get('status'),'chosen_plan':row.get('chosen_plan'),
        'token_status':'not_applicable_no_llm' if local else 'unknown','token_basis':'audited local native goal/assigned backend execution path' if local else 'unrecognized path; usage unavailable','llm_calls':0 if local else None,'tokens':{k:0 if local else None for k in TOKEN_KEYS},
        'time_seconds':{**{k:number(timings.get(k)) for k in PHASES},'decision_to_write':number(decision),'api_cumulative':number(diag.get('api_seconds'))},'api_reads':diag.get('api_reads'),'registered_probe_calls':row.get('total_probe_budget',{}).get('calls'),'registered_probe_cost':row.get('total_probe_budget',{}).get('cost'),'raw_episode':str(directory/'episode.json')}

def llm_records(path):
    # Supported journal: run_intervention_llm_controls.py, one started + one terminal event per index.
    events={}
    for line in Path(path).read_text().splitlines():
        try:e=json.loads(line)
        except json.JSONDecodeError:continue
        if 'index' not in e:raise ValueError('unsupported LLM journal identity')
        bucket=events.setdefault(e['index'],[]);bucket.append(e)
    result=[]
    for index,bucket in events.items():
        started=[e for e in bucket if e.get('status')=='started'];terminal=[e for e in bucket if e.get('status')!='started']
        if len(started)!=1 or len(terminal)>1:raise ValueError('duplicate LLM attempt ID; supply separate retry journals')
        e=terminal[0] if terminal else started[0];tokens=token_usage(e.get('usage'))
        result.append({'cohort':'llm_journal:'+str(Path(path).resolve()),'method':'llm:'+str(e.get('route','unknown')),'attempt_id':str(Path(path).resolve())+':'+str(index),'status':e.get('status'),'token_status':'provider_reported' if tokens['total_tokens'] is not None else 'unknown','token_basis':'provider usage, all started requests including failures retained','llm_calls':1,'tokens':tokens,'time_seconds':{'request':number(e.get('request_seconds'))},'raw_episode':str(path)})
    return result

def run(root,llm_events=()):
    root=Path(root).resolve();records=[];skipped=[]
    sources=[('pilot','pilot_legacy'),('pilot_v2','pilot'),('full_assigned','full_assigned'),('full_assigned_resume_cleanup_v1','full_assigned'),('full_online_cleanup_v1','full_policy'),('expiry_engineering_v1','development_expiry_engineering'),('../station_goal_transfer_20261003/preflight_v1','station_adapter_development'),('../station_goal_transfer_20261003/assigned_v1','station_transfer_assigned'),('../station_goal_transfer_20261003/policy_v1','station_transfer_policy')]
    for source,cohort in sources:
        for ledger in sorted((root/source/'campaigns').glob('*/episodes.jsonl')):
            config=json.loads((ledger.parent/'protocol.json').read_text())
            for index,line in enumerate(ledger.read_text().splitlines()):
                try:row=json.loads(line)
                except json.JSONDecodeError:skipped.append(str(ledger)+':in_progress_line');continue
                if row['status']=='started':continue
                records.append(native_record(ledger.parent,row,index,config,cohort))
    for path in llm_events:records.extend(llm_records(path))
    groups=defaultdict(list)
    for row in records:groups[(row['cohort'],row['method'])].append(row)
    methods=[]
    for (cohort,method),rows in sorted(groups.items()):
        times=sorted({k for r in rows for k in r['time_seconds']})
        methods.append({'cohort':cohort,'method':method,'attempts':len(rows),'statuses':dict(Counter(r['status'] for r in rows)),'token_statuses':dict(Counter(r['token_status'] for r in rows)),
          'tokens':{k:stats([r['tokens'][k] for r in rows]) for k in TOKEN_KEYS},'time_seconds':{k:stats([r['time_seconds'].get(k) for r in rows]) for k in times},'llm_calls':stats([r['llm_calls'] for r in rows]),'registered_probe_cost':stats([r.get('registered_probe_cost') for r in rows])})
    training_path=root/'neural_controls/RESULTS.json';training=None
    if training_path.exists():
        v=json.loads(training_path.read_text());training={'source':str(training_path),'resources':v.get('resources'),'models':[{'model':m['id'],'seed':m['seed'],'variant':m['variant'],'training_seconds':m['seconds'],'tokens':{k:0 for k in TOKEN_KEYS},'token_status':'not_applicable_no_llm'} for m in v['models']], 'primary_transition_fit_seconds':None,'primary_transition_fit_note':'historical fit wallclock not recorded; do not substitute zero'}
    launch_path=root/'full_master/LAUNCH.json';end_path=root/'FULL_RESULTS.json'
    pipeline=None
    if launch_path.exists():
        launch=json.loads(launch_path.read_text());finished=json.loads(end_path.read_text()).get('created_at') if end_path.exists() else None
        pipeline={'started_at':launch['started_at'],'ended_at':finished,'elapsed_wall_seconds':(finished or time.time())-launch['started_at'],'scope':'Cart full-matrix launch through its report/check time, including pauses/repair; excludes subsequent engineering and Station cohorts; not sum of parallel trials'}
    report={'schema':'method_token_time_cost_v1','checked_at':time.time(),'scope':'algorithm experiment costs only; Codex development-session tokens excluded','status':'incremental_closed_attempts','source_cohorts':sources,'skipped_in_progress':skipped,'methods':methods,'training':training,'records':records,'pipeline_wallclock':pipeline,
        'accounting_rules':['llm_calls denotes journaled request attempts; a started-only record does not prove provider execution', 'tokens use provider usage or audited no-LLM paths; unavailable is null, never zero','cached and reasoning counts are subsets; do not add again to total','all failures and started LLM requests retained; missing usage prevents an exact total','phase/API/decision measurements overlap; do not sum them with total_attempt','sum of attempts is cumulative work duration, not concurrent campaign elapsed time','p95 uses nearest rank; all timing summaries expose known and missing counts','standalone neural inference wallclock was not instrumented in old evaluator; report unknown, not training time or online decision time']}
    station_root=root.parent/'station_goal_transfer_20261003';inference=station_root/'evaluation_v1/INFERENCE_COSTS.json'
    report['station_transfer_inference_costs']=json.loads(inference.read_text()) if inference.exists() else None
    report['station_transfer_inference_scope']='prospective2threadCPUevaluation stream; not online latency, no Station fitting'
    save(root/'METHOD_COSTS.json',report)
    stream=io.StringIO();writer=csv.writer(stream);writer.writerow(['cohort','method','attempts','token_total_known_sum','token_total_known_n','token_total_missing_n','decision_to_write_known_n','decision_to_write_missing_n','decision_to_write_mean_seconds','decision_to_write_median_seconds','decision_to_write_p95_seconds','attempt_known_n','attempt_missing_n','attempt_mean_seconds','attempt_cumulative_seconds','api_known_n','api_missing_n','api_cumulative_mean_seconds','request_known_n','request_missing_n','request_mean_seconds','request_median_seconds','request_p95_seconds'])
    for m in methods:
        t=m['time_seconds'];d=t.get('decision_to_write',{});a=t.get('total_attempt',{});api=t.get('api_cumulative',{});tokens=m['tokens']['total_tokens'];request=t.get('request',{})
        writer.writerow([m['cohort'],m['method'],m['attempts'],tokens['sum'],tokens['known_n'],tokens['missing_n'],d.get('known_n',0),d.get('missing_n',m['attempts']),d.get('mean'),d.get('median'),d.get('p95'),a.get('known_n',0),a.get('missing_n',m['attempts']),a.get('mean'),a.get('sum'),api.get('known_n',0),api.get('missing_n',m['attempts']),api.get('mean'),request.get('known_n',0),request.get('missing_n',m['attempts']),request.get('mean'),request.get('median'),request.get('p95')])
    csv_path=root/'METHOD_COSTS.csv';tmp=csv_path.with_suffix('.csv.tmp');tmp.write_text(stream.getvalue());tmp.replace(csv_path)
    print(json.dumps({'status':'updated','closed_attempts':len(records),'report':str(root/'METHOD_COSTS.json')}))
    return report
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--llm-events',action='append',default=[]);a=p.parse_args();run(a.root,a.llm_events)
