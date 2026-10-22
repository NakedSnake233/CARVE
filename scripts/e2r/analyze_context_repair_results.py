#!/usr/bin/env python3
"""Read-only descriptive closeout; no model updates or changes to original scores."""
import argparse,collections,csv,json,statistics,time
from pathlib import Path

def read(p):return json.loads(Path(p).read_text())
def stats(values):
 known=[v for v in values if v is not None]
 return {'known':len(known),'missing':len(values)-len(known),'mean':statistics.mean(known) if known else None,'median':statistics.median(known) if known else None}
def run(root):
 root=Path(root);cost=read(root/'COSTS_FROZEN.json');stages={s:read(root/(s+'_v1')/'RESULTS.json') for s in ('training','validation','test','policy')};fit=read(root/'model_v1/RESULTS.json');test=read(root/'evaluation_v1/RESULTS.json')
 out={'checked_at':time.time(),'scope':'Descriptive registered context-repair slice. No test tuning, no superiority or noninferiority inference. Original raw/gated metrics retain different denominators.','stages':{},'fit_cost':fit['cost'],'model_selection':fit['selection']}
 negatives=[]
 for name,value in stages.items():
  records=value['records'];out['stages'][name]={'positions':len(records),'statuses':dict(collections.Counter(r['status'] for r in records)),'restored':sum(r['restored'] for r in records),'known_unsafe':sum(r['non_target_safe'] is False for r in records),'unknown':sum(r['unknown'] for r in records),'cells':value['cells']}
  for r in records:
   if r['non_target_safe'] is False or (name=='policy' and r['selected_plan']!='no_action' and not r['joint_success']):
    ep=Path(r['attempt_id']);raw=read(ep/'episode.json');v=raw.get('outcome',{}).get('verification',{})
    windows={}
    for key in ('business','peer'):
     p=ep/('post_'+key+'.json')
     if p.exists():windows[key]=read(p)['windows']
    negatives.append({**r,'windows':windows,'verification_status':{key:v.get(key) for key in ('non_target_safety','mitigation','capacity_status')},'during_strategy_status':{key:x['status'] for key,x in v.get('during_strategy_non_target',{}).items()},'during_execution_status':{key:x['status'] for key,x in v.get('during_execution_non_target',{}).items()}})
 out['negative_attempts']=negatives
 out['prediction_metrics']={}
 for name,block in [('training',fit['train']),('validation',fit['validation']),('test',test)]:
  o=block['overall'];out['prediction_metrics'][name]={head:{scope:{target:{k:o[head][scope][target][k] for k in ('total','known_labels','missing_labels','scored','known_but_prediction_unknown','brier','ece')} for target in ('success','safety')} for scope in ('raw','after_structural_gate')} for head in ('context','coarse')}
 out['test_rollback_by_fault']={}
 for fault in sorted({p['fault'] for p in test['predictions']}):
  ps=[p for p in test['predictions'] if p['fault']==fault and p['operation']=='rollback_version']
  out['test_rollback_by_fault'][fault]={'n':len(ps),'context_success':[p['context']['success'] for p in ps],'coarse_success':[p['coarse']['success'] for p in ps],'observed_success':[p['labels']['success'] for p in ps],'gate':[p['gate_status'] for p in ps]}
 records=stages['policy']['records'];out['policy']={};comparisons={}
 for method in ('goal_reuse','goal_fresh','expert_fresh','blind_fresh'):
  rs=[r for r in records if r['method']==method];cs=[c for c in cost['records'] if c['stage']=='policy' and c['method']==method];raw=[read(c['raw_episode']) for c in cs]
  out['policy'][method]={'n':len(rs),'business_recovered':sum(r['business_recovered'] is True for r in rs),'safe':sum(r['non_target_safe'] is True for r in rs),'joint_success':sum(r['joint_success'] is True for r in rs),'actions':dict(collections.Counter(r['selected_plan'] for r in rs)),
   'probe_cost':stats([c['registered_probe_cost'] for c in cs]),'probe_calls':sum(c['registered_probe_calls'] for c in cs),'api_reads':stats([c['api_reads'] for c in cs]),'api_seconds':stats([c['time_seconds']['api_cumulative'] for c in cs]),
   'write_prepared_seconds':stats([c['time_seconds']['decision_to_write'] for c in cs]),'total_attempt_seconds':stats([c['time_seconds']['total_attempt'] for c in cs]),'observation_plus_decision_seconds':stats([c['time_seconds']['pre_action_observation']+c['time_seconds']['refinement_and_decision'] for c in cs]),'tokens':sum(c['tokens']['total_tokens'] for c in cs),'expiry_retry':sum('expired_reuse_retry' in x.get('execution',{}) for x in raw)}
  comparisons[method]={(r['fault'],r['state_condition']):r for r in rs}
 out['action_agreement']={method:{'same':sum(r['selected_plan']==comparisons['goal_reuse'][key]['selected_plan'] for key,r in comp.items()),'n':len(comp)} for method,comp in comparisons.items() if method!='goal_reuse'}
 out['reuse_deltas']={}
 ours=out['policy']['goal_reuse']
 for m,c in out['policy'].items():
  if m=='goal_reuse':continue
  out['reuse_deltas'][m]={'probe_mean_reduction':1-ours['probe_cost']['mean']/c['probe_cost']['mean'] if c['probe_cost']['mean'] else None,'write_prepared_median_reduction_writes_only':1-ours['write_prepared_seconds']['median']/c['write_prepared_seconds']['median'],'total_attempt_mean_reduction':1-ours['total_attempt_seconds']['mean']/c['total_attempt_seconds']['mean'],'scope':'4 writing positions for prepared median;8 positions for means. Rules/coarse choose a different action in readiness/shared, so cost difference versus them does not isolate reuse.'}
 out['denominators']={'native_positions':sum(s['positions'] for s in out['stages'].values()),'cost_records':len(cost['records']),'unknown':sum(s['unknown'] for s in out['stages'].values()),'known_unsafe':sum(s['known_unsafe'] for s in out['stages'].values()),'online_no_action':sum(r['selected_plan']=='no_action' for r in records)}
 (root/'ANALYSIS.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
 with (root/'POLICY_COMPARISON.csv').open('w',newline='') as f:
  writer=csv.writer(f);writer.writerow(['method','n','business_recovered','safe','joint_success','probe_cost_mean','write_prepared_median_seconds','write_timing_known','total_attempt_mean_seconds','llm_tokens'])
  for m,v in out['policy'].items():writer.writerow([m,v['n'],v['business_recovered'],v['safe'],v['joint_success'],v['probe_cost']['mean'],v['write_prepared_seconds']['median'],v['write_prepared_seconds']['known'],v['total_attempt_seconds']['mean'],v['tokens']])
 return out
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',required=True);a=p.parse_args();out=run(a.root);print(json.dumps({k:out[k] for k in ('denominators','action_agreement','reuse_deltas')}))
