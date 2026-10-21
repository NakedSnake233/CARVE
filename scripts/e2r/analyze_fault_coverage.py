#!/usr/bin/env python3
"""Descriptive closeout from immutable native rows; no fitting or threshold edits."""
import argparse,collections,csv,json,statistics,time
from pathlib import Path

def read(p):return json.loads(Path(p).read_text())
def save(p,d):Path(p).write_text(json.dumps(d,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def analyze(root):
 root=Path(root);results=read(root/'RESULTS.json');cost=read(root/'COSTS_FROZEN.json');out={'checked_at':time.time(),'scope':'Descriptive developmental coverage; policy1repeat/cell, no significance/noninferiority claim. Original results unchanged.','stages':{},'cost_source':'COSTS_FROZEN.json'}
 for stage,value in results['stages'].items():
  groups=collections.defaultdict(list)
  for row in value['records']:groups[row['method']].append(row)
  methods={}
  for method,rs in sorted(groups.items()):
   methods[method]={'n':len(rs),'joint_success':sum(r['joint_success'] for r in rs),'business_success':sum(r['business_recovered'] is True for r in rs),'safe_known':sum(r['non_target_safe'] is True for r in rs),'unsafe_known':sum(r['non_target_safe'] is False for r in rs),'unknown':sum(r['business_recovered'] is None for r in rs),'actions':dict(collections.Counter(r['selected_plan'] for r in rs))}
  out['stages'][stage]={'positions':len(value['records']),'methods':methods,'cells':value['cells']}
 policy=results['stages']['policy']['records'];lookup={(r['fault'],r['state_condition'],r['method']):r for r in policy};configs=sorted({(r['fault'],r['state_condition']) for r in policy})
 out['policy_action_agreement']={m:{'same_actions':sum(lookup[(*c,'goal_reuse')]['selected_plan']==lookup[(*c,m)]['selected_plan'] for c in configs),'configuration_cells':len(configs)} for m in ['goal_fresh','expert_fresh','blind_fresh']}
 out['policy_costs']={}
 for m in out['stages']['policy']['methods']:
  rs=[r for r in cost['records'] if r['cohort']=='fault_coverage_policy' and r['method']==m]
  summary=next(c for c in cost['methods'] if c['cohort']=='fault_coverage_policy' and c['method']==m)
  out['policy_costs'][m]={'n':len(rs),'mean_probe_cost':summary['probe_cost']['mean'],'median_decision_to_write_s':summary['time_seconds']['decision_to_write']['median'],'mean_total_attempt_s':summary['time_seconds']['total_attempt']['mean'],'median_total_attempt_s':summary['time_seconds']['total_attempt']['median'],'mean_api_seconds':summary['time_seconds']['api_cumulative']['mean'],'mean_api_reads':statistics.mean(r['api_reads'] for r in rs),'tokens':sum(r['tokens']['total_tokens'] for r in rs)}
 ours=out['policy_costs']['goal_reuse'];out['reuse_comparisons']={}
 for m,c in out['policy_costs'].items():
  if m=='goal_reuse':continue
  out['reuse_comparisons'][m]={k:(1-ours[k]/c[k] if c[k] else None) for k in ['mean_probe_cost','median_decision_to_write_s','mean_total_attempt_s','median_total_attempt_s']}
 # Keep all negatives. Diagnostics describe measurements, not causal blame.
 diagnostics=[]
 for stage,value in results['stages'].items():
  for record in value['records']:
   if record['joint_success']:continue
   ep=Path(record['attempt_id']);row=read(ep/'episode.json');v=row.get('outcome',{}).get('verification',{})
   detail={**record,'verification':{k:v.get(k) for k in ['non_target_safety','mitigation','endpoints_status','mechanism_eliminated']},'windows':{},'execution_status':row.get('execution',{}).get('status')}
   for name in ['business','peer']:
    p=ep/('post_'+name+'.json')
    if not p.exists():continue
    data=read(p);windows=[]
    for w,raw in zip(data['windows'],data['raw']):
     failures=collections.Counter()
     for tx in raw.get('transaction_results',[]):
      if not tx.get('ok',tx.get('success',True)):failures[str(tx.get('diagnostics',{}).get('failure',tx.get('error','unspecified')))]+=1
     windows.append({k:w[k] for k in ['window_id','start_time','end_time','p95_ms','error_rate','throughput_rps','state_canary_ok']}|{'failure_diagnostics':dict(failures)})
    detail['windows'][name]=windows
   diagnostics.append(detail)
 out['negative_attempts']=diagnostics
 out['denominators']={'positions':results['positions'],'closed_cost_records':len(cost['records']),'observed':sum(r['status']=='observed' for s in results['stages'].values() for r in s['records']),'unknown':sum(r['business_recovered'] is None for s in results['stages'].values() for r in s['records']),'known_unsafe':sum(r['non_target_safe'] is False for s in results['stages'].values() for r in s['records'])}
 save(root/'ANALYSIS.json',out)
 with (root/'POLICY_COMPARISON.csv').open('w',newline='') as f:
  fields=['method',*next(iter(out['policy_costs'].values())).keys(),'joint_success','business_success','safe_known','unsafe_known'];writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
  for m,c in out['policy_costs'].items():writer.writerow({'method':m,**c,**{k:out['stages']['policy']['methods'][m][k] for k in fields[-4:]}})
 return out
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',required=True);a=p.parse_args();d=analyze(a.root);print(json.dumps({k:d[k] for k in ['denominators','policy_action_agreement','reuse_comparisons']}))
