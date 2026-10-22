#!/usr/bin/env python3
"""Fixed all-attempt analysis of the registered native online strategy matrix."""
import argparse
from collections import Counter,defaultdict
import csv
import json
from pathlib import Path
import random
import statistics
import sys
import time
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.data import validate_record

STRATEGIES=('decision','random','fixed','none')
METRICS=('recovery_lower','recovery_upper','harm_lower','harm_upper','joint_lower','joint_upper',
         'executed','active_calls','active_cost','total_calls','total_cost','decision_seconds','attempt_seconds')


def dump(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def bounds(value,positive=True):
    if value is None:return (0.,1.)
    return (float(value is positive),)*2


def attempt(row,config):
    e=row.get('outcome',{}).get('effects',{});recovery=e.get('business_recovered');safe=e.get('non_target_safe')
    joint=True if recovery is True and safe is True else False if recovery is False or safe is False else None
    decision=row.get('decision',{});budget=decision.get('budget',{});total=row.get('total_probe_budget',{})
    observations=decision.get('observations',[])
    change=sum(o.get('candidate_plan_after')!=o.get('selected_plan_before') for o in observations if 'candidate_plan_after' in o)
    catalog={p['id']:p for p in config['plans']}
    r={'attempt_id':row['episode_id'],'application':row['application'],'parent':row['comparison_group'],
       'condition':row['condition_id'],'seed':config['prediction_seeds'][0],'repeat':row['repeat'],
       'slot':row['original_schedule_slot'],'strategy':row['assigned_method'],'protocol':row['protocol_stratum'],
       'status':row['status'],'execution_status':row.get('execution',{}).get('status'),
       'chosen_plan':row['chosen_plan'],'selected_action':decision.get('decision')=='execute',
       'executed':row.get('execution',{}).get('status')=='executed',
       'recovered':recovery,'safe':safe,'joint':joint,'restore_status':row.get('restore',{}).get('status'),
       'active_calls':budget.get('calls'),'active_cost':budget.get('cost'),'total_calls':total.get('calls'),
       'total_cost':total.get('cost'),'probe_seconds':total.get('probe_seconds'),
       'decision_seconds':budget.get('seconds'),'attempt_seconds':row.get('timings_seconds',{}).get('total_attempt'),
       'predictor_calls':decision.get('predictor_work',{}).get('calls'),
       'predictor_wall_seconds':decision.get('predictor_work',{}).get('seconds'),
       'query_decision_changes':change,'queries_with_change_record':sum('candidate_plan_after' in o for o in observations),
       'strict_nonempty_candidate_eligible':any(c.get('strict_admission_diagnostic',{}).get('candidate_eligible') is True
           and catalog[c['plan_id']]['actions'] for c in decision.get('candidates',[])),
       'qualified_profile':any(c.get('strict_admission_diagnostic',{}).get('qualified_profile') is True for c in decision.get('candidates',[])),
       'total_runtime_exceeded':total.get('actual_runtime_exceeded'), 'decision_over_budget':decision.get('budget_exceeded'),
       'phase_timings':row.get('timings_seconds',{}),'registered_phases':{}}
    for key,value,positive in [('recovery',recovery,True),('harm',safe,False),('joint',joint,True)]:
        r[key+'_lower'],r[key+'_upper']=bounds(value,positive)
    for phase in ('initial','adaptive','mandatory'):
        events=[x for x in total.get('events',[]) if x.get('phase')==phase and x.get('started')]
        r['registered_phases'][phase]={'calls':len(events),'cost':sum(x['declared_cost'] for x in events),
                                     'seconds':sum(x.get('seconds',0.) for x in events)}
    return r


def continuous(values):
    known=[float(v) for v in values if v is not None]
    return {'n':len(values),'known':len(known),'missing':len(values)-len(known),
            'mean':statistics.mean(known) if known else None,'total':sum(known) if known else None,
            'sample_std':statistics.stdev(known) if len(known)>1 else None}


def summarize(rows):
    selected=[r for r in rows if r['executed']]
    result={'attempts':len(rows),'executed_count':len(selected),
            'execution_fraction':len(selected)/len(rows) if rows else None,
            'status_counts':dict(Counter(r['status'] for r in rows)),
            'execution_statuses':dict(Counter(r['execution_status'] for r in rows)),
            'chosen_plans':dict(Counter(r['chosen_plan'] for r in rows)),
            'strict_nonempty_candidate_eligible_count':sum(r['strict_nonempty_candidate_eligible'] for r in rows),
            'qualified_profile_count':sum(r['qualified_profile'] for r in rows),
            'restore_failures':sum(r['restore_status']!='restored' for r in rows),
            'decision_budget_exceeded':sum(r['decision_over_budget'] is True for r in rows),
            'total_runtime_exceeded':sum(r['total_runtime_exceeded'] is True for r in rows),
            'query_decision_changes':sum(r['query_decision_changes'] for r in rows)}
    for key in ('recovered','safe','joint'):
        result[key]={'true':sum(r[key] is True for r in rows),'false':sum(r[key] is False for r in rows),
                     'unknown':sum(r[key] is None for r in rows),'denominator':len(rows)}
    result['population_bounds']={k:statistics.mean(r[k] for r in rows) if rows else None for k in METRICS[:6]}
    result['executed_conditional_bounds']={k:statistics.mean(r[k] for r in selected) if selected else None for k in METRICS[:6]}
    result['costs']={k:continuous([r[k] for r in rows]) for k in (*METRICS[7:],'probe_seconds','predictor_calls','predictor_wall_seconds')}
    result['phase_timings']={k:continuous([r['phase_timings'].get(k) for r in rows]) for k in sorted({k for r in rows for k in r['phase_timings']})}
    result['registered_phases']={p:{k:continuous([r['registered_phases'][p][k] for r in rows]) for k in ('calls','cost','seconds')} for p in ('initial','adaptive','mandatory')}
    return result


def paired(rows,left,right,*,repetitions=2000,seed=42):
    index=defaultdict(dict)
    for r in rows:
        if r['strategy'] not in (left,right):continue
        key=(r['application'],r['parent'],r['condition'],r['seed'],r['repeat'])
        if r['strategy'] in index[key]:raise ValueError('duplicate strategy inside paired block; preserve ancestry and review')
        index[key][r['strategy']]=r
    if any(set(v)!={left,right} for v in index.values()):raise ValueError('missing registered paired strategy')
    differences=defaultdict(lambda:defaultdict(list))
    for key,cell in index.items():
        a,b=cell[left],cell[right]
        if a['protocol']!=b['protocol']:raise ValueError('paired observation protocol mismatch')
        group=key[0]+'/'+key[1]
        for metric in METRICS:
            if a[metric] is not None and b[metric] is not None:differences[group][metric].append(float(a[metric])-float(b[metric]))
    result={'left':left,'right':right,'matched_blocks':len(index),'groups':len(differences),'seed':seed,'bootstrap_repetitions':repetitions,
            'scope':'Left minus right, same application/parent/condition/modelseed/repeat; whole known configuration groups resampled. Four groups only; descriptive intervals, not confirmatory population confidence or noninferiority proof.', 'metrics':{}}
    keys=sorted(differences)
    for metric in METRICS:
        means={g:statistics.mean(differences[g][metric]) for g in keys if differences[g][metric]}
        item={'matched_known_blocks':sum(len(differences[g][metric]) for g in keys),
              'unscored_blocks':len(index)-sum(len(differences[g][metric]) for g in keys),
              'group_means':means,'difference':statistics.mean(means.values()) if means else None,'ci95':None}
        if len(means)==len(keys) and len(keys)>=2:
            rng=random.Random(seed);samples=sorted(statistics.mean(means[rng.choice(keys)] for _ in keys) for _ in range(repetitions))
            item['ci95']=[samples[int(.025*repetitions)],samples[min(repetitions-1,int(.975*repetitions))]]
        result['metrics'][metric]=item
    return result


def analyze(data,out):
    data,out=Path(data),Path(out)
    raw=[json.loads(l) for l in (data/'episodes.jsonl').read_text().splitlines()];sources=json.loads((data/'sources.json').read_text())
    summary=json.loads((data/'summary.json').read_text());configs={s['source_directory']:json.loads((Path(s['source_directory'])/'protocol.json').read_text()) for s in sources}
    records=[]
    for row in raw:
        validate_record(row)
        records.append(attempt(row,configs[row['source_provenance']['source_directory']]))
    if len(records)!=480 or len({(r['application'],r['parent'],r['slot']) for r in records})!=480:raise ValueError('expected exactly480 unique registered slots')
    if Counter(r['strategy'] for r in records)!=Counter({s:120 for s in STRATEGIES}):raise ValueError('strategy matrix incomplete')
    reports={s:summarize([r for r in records if r['strategy']==s]) for s in STRATEGIES}
    seeds={str(seed):{s:summarize([r for r in records if r['strategy']==s and r['seed']==seed]) for s in STRATEGIES} for seed in (42,43,44)}
    groups={g:{s:summarize([r for r in records if r['application']+'/'+r['parent']==g and r['strategy']==s]) for s in STRATEGIES}
            for g in sorted({r['application']+'/'+r['parent'] for r in records})}
    all_comparisons=[paired(records,a,b) for i,a in enumerate(STRATEGIES) for b in STRATEGIES[i+1:]]
    costs=[]
    for source in sources:
        raw_summary=json.loads((Path(source['source_directory'])/'summary.json').read_text());restore=raw_summary['campaign_baseline_restore']
        costs.append({'source_id':source['source_id'],'historical_summary_status':raw_summary['status'],
                      'historical_final_restore_status':restore['status'],'later_recovery_verified':'terminal_health_recovery' in source,
                      'parent_rss_mib':restore.get('laboratory_process_peak_rss_mib'),'child_rss_mib':restore.get('maximum_child_rss_mib'),
                      'cpu':restore.get('process_cpu_seconds',{})})
    result={'schema':'native_online_strategy_analysis_v1','status':'complete','created_at':time.time(),'data_summary':summary,
            'strategies':reports,'by_seed':seeds,'by_group':groups,'paired_comparisons':all_comparisons,'campaign_cost_and_recovery':costs,
            'scope':'480 laboratory-authorized online trials,4 known configuration groups,3modelseeds. No qualified-profile release. No comparison against a new learned graph/noedge model or unseen configurations.',
            'uncertainty':'Unknown labels retained in pessimistic/optimistic outcome bounds, which are not sampling confidence bounds. Query costs report their known denominator. Nonexecuted conditional risks are undefined.',
            'cpu_scope':'CPU and peakRSS measured per4-strategy campaign process; not attributable to an individual strategy; native Pods excluded. Decision predictor_seconds is wall time, not CPU.'}
    if any((out/name).exists() for name in ('RESULTS.json','ATTEMPTS.json','ATTEMPTS.csv','RESULTS.md')):
        raise ValueError('refuse to overwrite analysis artifacts')
    out.mkdir(parents=True,exist_ok=True);dump(out/'RESULTS.json',result);dump(out/'ATTEMPTS.json',records)
    flat=[{k:v for k,v in r.items() if not isinstance(v,dict)} for r in records]
    with (out/'ATTEMPTS.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(flat[0]));w.writeheader();w.writerows(flat)
    lines=['# 在线策略对照结果（480次）','','范围：已知配置、原生依赖切片、实验授权执行；不等同于严格准入资格。每策略120次，所有分配位置均计入分母。','',
           '| 策略 | 实际执行动作 | 业务恢复 | 非目标安全 | 同时恢复且安全 | 主动调用均值 | 主动成本均值 | 总调用均值 | 总成本均值 | 决策秒数均值 |',
           '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for s,r in reports.items():
        lines.append(f"| {s} | {r['executed_count']}/120 | {r['recovered']['true']}/120 | {r['safe']['true']}/120 | {r['joint']['true']}/120 | "+' | '.join(f"{r['costs'][k]['mean']:.3f}" for k in ('active_calls','active_cost','total_calls','total_cost','decision_seconds'))+' |')
    lines+=['','## 配对差异（decision减对照）','','按完整配置组bootstrap2000次，随机种子42；仅4个已知组，区间仅作描述。重复/模型种子不是独立配置组。','',
            '| 对照 | 恢复率差/描述性95%区间 | 同时恢复安全差/区间 | 总查询成本差/区间 | 决策秒数差/区间 |','|---|---|---|---|---|']
    for c in all_comparisons:
        if c['left']!='decision':continue
        cells=[]
        for k in ('recovery_lower','joint_lower','total_cost','decision_seconds'):
            x=c['metrics'][k];cells.append(f"{x['difference']:.4f} / {x['ci95']}")
        lines.append('| '+c['right']+' | '+' | '.join(cells)+' |')
    lines+=['','## 边界与历史失败','','- 实验选动作率、严格候选诊断和合格profile分列；所有profile未获资格。',
            '- 每策略/种子/配置的结果、unknown上下界、全部配对、成本、动作变化、CPU/RSS与阶段时间见RESULTS.json；480条明细见ATTEMPTS.csv/JSON。',
            '- 历史1个campaign结束canary失败及后续恢复凭据保留；不改写原summary，也不重跑其4条已完成记录。另保留曾导致队列停止的检查器格式误判日志。',
            '- 三种子checkpoint和业务/安全门槛未由在线结果调整。现有结果不能直接推广到完整应用或独立新配置；C1/C2等待fresh审查。']
    (out/'RESULTS.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'status':'complete','attempts':480,'strategies':{s:{'executed':r['executed_count'],'recovered':r['recovered'],'safe':r['safe'],'active_calls':r['costs']['active_calls']['mean'],'total_cost':r['costs']['total_cost']['mean']} for s,r in reports.items()}}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data',required=True);p.add_argument('--out',required=True);a=p.parse_args();analyze(a.data,a.out)
