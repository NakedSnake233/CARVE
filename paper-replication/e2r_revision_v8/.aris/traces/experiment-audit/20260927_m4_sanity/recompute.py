"""Offline forensic recomputation. Never calls external systems."""
from pathlib import Path
import datetime as dt, hashlib, json, math, statistics, sys
from unittest.mock import patch
ROOT=Path('${PROJECT_ROOT}');sys.path.insert(0,str(ROOT))
OUT=Path(__file__).resolve().parent
from methods.PlanRCAThinkRemed import safety_guard, repair_planner_agent, playbook_generator_agent
from methods.PlanRCAThinkRemed.schemas import RepairPlan
from methods.PlanRCAThinkRemed.cluster_client import ClusterClientContext
from rca_bench.recovery_oracle import WorkloadWindow,evaluate_recovery
from scripts.e2r.run_recovery_sanity import validate_static_bindings
NS='e2r-supplement-20260925';CTX=ClusterClientContext('${SCOPED_KUBECONFIG}')
checks=[];windows=[];baselines={};runs={}
def check(label, result, **detail):
 checks.append({'check':label,'passed':bool(result),**detail})
def h(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def approx(a,b,tol=1e-10):return math.isclose(a,b,rel_tol=tol,abs_tol=tol)
def window_check(tag,w,r):
 lat=r['latencies_ms']; n=r['attempts'];s=r['successes'];elapsed=r['elapsed_seconds']
 valid=[v for v in lat if type(v) in (int,float) and math.isfinite(v) and v>=0]
 check(tag+':all_attempt_latencies',len(valid)==n==w['samples']==r['valid_latency_samples'])
 check(tag+':success_domain',type(s) is int and 0<=s<=n)
 p95=sorted(valid)[math.ceil(.95*n)-1]; throughput=s/elapsed;err=(n-s)/n
 check(tag+':p95',p95==w['p95_ms']); check(tag+':throughput',throughput==w['throughput_rps']);check(tag+':error',err==w['error_rate'])
 check(tag+':scheduled_count',n==math.ceil(r['offered_rps']*r['target_window_seconds']))
 check(tag+':elapsed',elapsed>=r['target_window_seconds'] and abs(elapsed-(w['end_time']-w['start_time']))<.01)
 check(tag+':identity',r['window_id']==w['window_id'] and w['source_id']==f'k8s://{NS}/service/frontend/http-root')
 windows.append({'artifact':tag.split('/')[0],'window_id':w['window_id'],'samples':n,'successes':s,'error_rate':err,'p95_ms':p95,'throughput_rps':throughput,'elapsed_seconds':elapsed,'start_time':w['start_time'],'end_time':w['end_time'],'state_canary_ok':w['state_canary_ok']})
for ver in (1,2):
 p=ROOT/f'experiments/e2r/runs/recovery_baseline_v{ver}/baseline.json';d=json.loads(p.read_text());baselines[ver]=d
 for w,r in zip(d['windows'],d['raw_window_diagnostics']):window_check(f'baseline_v{ver}/{w["window_id"]}',w,r)
 check(f'baseline_v{ver}:six_raw_windows',len(d['windows'])==len(d['raw_window_diagnostics'])==6)
 check(f'baseline_v{ver}:ordered_windows',all(a['end_time']<=b['start_time'] for a,b in zip(d['windows'],d['windows'][1:])))
 lat=sorted(v for r in d['raw_window_diagnostics'] for v in r['latencies_ms']);b=d['baseline']
 check(f'baseline_v{ver}:aggregate_p95',b['p95_ms']==lat[math.ceil(.95*len(lat))-1]);check(f'baseline_v{ver}:aggregate_count',b['samples']==len(lat)==900)
 check(f'baseline_v{ver}:aggregate_throughput',b['throughput_rps']==statistics.median(w['throughput_rps'] for w in d['windows']))
 check(f'baseline_v{ver}:aggregate_error',b['error_rate']==sum(r['attempts']-r['successes'] for r in d['raw_window_diagnostics'])/len(lat))
 check(f'baseline_v{ver}:no_fault',d['fault_injected'] is False and d['repair_executed'] is False)
 check(f'baseline_v{ver}:aggregate_times',b['start_time']==d['windows'][0]['start_time'] and b['end_time']==d['windows'][-1]['end_time'])
comp=json.loads((ROOT/'experiments/e2r/runs/recovery_baseline_comparison_v1/analysis.json').read_text());b1=baselines[1]['baseline'];b2=baselines[2]['baseline']
check('comparison:p95_ratio',comp['comparison']['p95_ratio_v2_over_v1']==b2['p95_ms']/b1['p95_ms']);check('comparison:throughput_ratio',comp['comparison']['throughput_ratio_v2_over_v1']==b2['throughput_rps']/b1['throughput_rps']);check('comparison:error_delta',comp['comparison']['error_rate_delta']==b2['error_rate']-b1['error_rate'])
for ver in (1,2,3):
 p=ROOT/f'experiments/e2r/runs/recovery_sanity_frontend_cpu_v{ver}/trace.json';d=json.loads(p.read_text());tag=f'sanity_v{ver}';basever=1 if ver<3 else 2;b=baselines[basever]['baseline'];runs[ver]={'status':d['status'],'fault_injected':d['fault_injected'],'repair_executed':d['repair_executed']}
 check(tag+':baseline_hash',d['baseline_sha256']==h(ROOT/f'experiments/e2r/runs/recovery_baseline_v{basever}/baseline.json'))
 for k in ('pre','fault'):
  if k+'_window' in d:window_check(tag+'/'+k,d[k+'_window'],d[k+'_raw'])
 for w,r in zip(d.get('post_windows',[]),d.get('post_raw',[])):window_check(tag+'/'+w['window_id'],w,r)
 w=d['pre_window'];healthy=(w['error_rate']<=b['error_rate']+.01 and w['p95_ms']<=b['p95_ms']*1.2 and w['throughput_rps']>=b['throughput_rps']*.8 and w['state_canary_ok'] is True)
 check(tag+':pre_gate',healthy==d['pre_health_gate']);check(tag+':baseline_precedes',b['end_time']<w['start_time']);check(tag+':attempt_times',d['started_at']<=w['start_time']<w['end_time']<=d['completed_at'])
 clean=d['cleanup'];absence=d['cleanup_absence_probe'];check(tag+':cleanup',clean['returncode']==0 and absence['returncode']==1 and 'NotFound' in absence['stderr'] and d['cleanup_verified_absent'] is True and d['cleanup_error'] is None and d['cleanup_absence_error'] is None)
 check(tag+':cleanup_target',clean['argv']==['kubectl','delete','stresschaos','e2r-sanity-cpu','-n',NS,'--ignore-not-found'] and absence['argv']==['kubectl','get','stresschaos','e2r-sanity-cpu','-n',NS,'-o','json'])
 check(tag+':scoped_context',d['client_context']==CTX.to_dict());check(tag+':rbac',d['rbac_checks']=={'isolated_get':True,'isolated_delete_chaos':True,'isolated_portforward':True,'user_namespace_denied':False})
 if not d['fault_injected']:
  check(tag+':no_injection_stage',d['events']==[] and not d['repair_executed'] and 'execution' not in d and 'oracle' not in d);continue
 ev=d['events'];check(tag+':one_injection',len(ev)==1 and ev[0]['stage']=='injection' and ev[0]['result']['returncode']==0)
 fw=d['fault_window'];degraded=(fw['error_rate']>b['error_rate']+.01 or fw['p95_ms']>b['p95_ms']*1.2 or fw['throughput_rps']<b['throughput_rps']*.8 or fw['state_canary_ok'] is False)
 check(tag+':degradation',degraded and d['initial_business_degradation'] and d['fault_window_measurable'])
 check(tag+':stage_times',w['end_time']<=d['injection_request_started_at']<=d['injection_completed_at']<fw['start_time']<fw['end_time']<d['repair_completed_at'])
 check(tag+':settle_30s',fw['start_time']-d['injection_completed_at']>=30)
 plan=RepairPlan.from_dict(d['repair_plan']);replanned=repair_planner_agent.build_repair_plan(rca_report=plan.rca_report,evidence_history=plan.rca_report.evidence,runtime_envs='isolated k3s benchmark',namespace=NS,env=NS,use_llm=False)
 check(tag+':reproduce_plan',replanned.to_dict()==plan.to_dict());check(tag+':plan_gate',safety_guard.check_repair_plan(plan,allowed_namespace=NS)==(True,[]))
 check(tag+':render_yaml',playbook_generator_agent.render_playbook(plan,client_context=CTX)==d['playbook']);check(tag+':yaml_gate',safety_guard.check_playbook_yaml(d['playbook'],allowed_namespace=NS,expected_commands=safety_guard.expected_playbook_commands(plan),client_context=CTX)==(True,[]))
 check(tag+':stored_gates',d['safety']=={'plan_ok':True,'yaml_ok':True,'reasons':[]})
 with patch('methods.PlanRCAThinkRemed.safe_probe_agent.run_probes',return_value=d['preconditions']['results']):
  replay=safety_guard.run_preconditions(plan,client_context=CTX)
 check(tag+':replayed_preconditions',replay==d['preconditions'])
 evidence_obj=json.loads(plan.rca_report.evidence[0].raw_excerpt);pre_obj=json.loads(d['preconditions']['results'][0]['stdout'])
 check(tag+':same_observed_object',evidence_obj==pre_obj)
 cond={r['type']:r['status'] for r in pre_obj['status']['conditions']};records=pre_obj['status']['experiment']['containerRecords']
 check(tag+':active_actual_snapshot',cond=={'Selected':'True','AllInjected':'True','AllRecovered':'False','Paused':'False'} and all(r['phase']=='Injected' and r['injectedCount']==1 and r['recoveredCount']==0 for r in records))
 check(tag+':actual_target',pre_obj['metadata']['namespace']==NS and pre_obj['metadata']['name']=='e2r-sanity-cpu' and pre_obj['kind']=='StressChaos' and pre_obj['spec']=={'duration':'180s','mode':'one','selector':{'labelSelectors':{'app':'frontend'},'namespaces':[NS]},'stressors':{'cpu':{'load':100,'workers':10}}})
 t0=d['injection_request_started_at'];completed=d['repair_completed_at']-t0;execution=d['execution']
 check(tag+':conservative_deadline',d['pre_repair_fault_age_seconds']<120 and d['pre_execution_fault_age_seconds']<150 and completed<150 and execution['repair_completion_fault_age_seconds']<150 and execution['completed_before_attribution_deadline'] is True)
 check(tag+':monotonic_wall_agree',abs(completed-execution['repair_completion_fault_age_seconds'])<.01)
 check(tag+':timeout_budget',d['remaining_attribution_budget_seconds']==150-d['pre_execution_fault_age_seconds'] and d['ansible_timeout_seconds']==min(30,max(0,math.floor(d['remaining_attribution_budget_seconds']))))
 runs[ver].update({'chaos_uid':pre_obj['metadata']['uid'],'active_target_records':records,'repair_completion_age_seconds':completed,'execution_returncode':execution['returncode'],'cleanup_absent':True,'baseline_version':basever,'threshold_p95_ms':b['p95_ms']*1.2})
 if ver==1:
  check(tag+':infrastructure_failure',execution['returncode']==1 and 'ModuleNotFoundError' in execution['stderr'] and d['status']=='failed' and not d['repair_executed'] and 'oracle' not in d and 'post_windows' not in d)
  check(tag+':rollback_not_retry',d['rollback']['status']=='PASS' and len(d['rollback']['results'])==1 and d['rollback']['results'][0]['stdout'].endswith(' unchanged'))
 if ver==3:
  check(tag+':delete_execution',execution['returncode']==0 and 'deleted from '+NS+' namespace' in execution['stdout'] and d['repair_executed'])
  oracle=evaluate_recovery(baseline=WorkloadWindow(**b),post_windows=[WorkloadWindow(**w) for w in d['post_windows']],technical=d['technical_checks'],required_windows=3,require_state_canary=True,repair_completed_at=d['repair_completed_at'],evaluated_at=d['oracle']['evaluated_at'],max_window_age_seconds=120)
  check(tag+':oracle_replay',oracle==d['oracle']);check(tag+':oracle_pass',oracle['status']=='PASS')
  check(tag+':all_posts_before_expiry',all(w['end_time']<t0+180 for w in d['post_windows']))
  check(tag+':eval_before_expiry',d['oracle']['evaluated_at']<t0+180)
  runs[ver]['post_end_fault_ages_seconds']=[w['end_time']-t0 for w in d['post_windows']];runs[ver]['freshness_seconds']=[d['oracle']['evaluated_at']-w['end_time'] for w in d['post_windows']];runs[ver]['oracle_reproduced']=oracle
bpath=ROOT/'experiments/e2r/runs/recovery_baseline_v2/baseline.json';mpath=ROOT/'experiments/e2r/isolated_boutique.yaml'
bind=validate_static_bindings(bpath,bpath.read_bytes(),mpath,mpath.read_bytes());v3=json.loads((ROOT/'experiments/e2r/runs/recovery_sanity_frontend_cpu_v3/trace.json').read_text())
check('v3:static_bindings',all(v3[k]==v for k,v in bind.items()))
for label,bb,mm,bp in [('bad_baseline_bytes',bpath.read_bytes()+b' ',mpath.read_bytes(),bpath),('bad_manifest_bytes',bpath.read_bytes(),mpath.read_bytes()+b' ',bpath),('old_baseline_path',bpath.read_bytes(),mpath.read_bytes(),ROOT/'experiments/e2r/runs/recovery_baseline_v1/baseline.json')]:
 try:validate_static_bindings(bp,bb,mpath,mm)
 except ValueError:check('binding_rejection:'+label,True)
 else:check('binding_rejection:'+label,False)
report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'checks':checks,'failed_checks':[c for c in checks if not c['passed']],'raw_window_count':len(windows),'raw_attempts':sum(w['samples'] for w in windows),'windows':windows,'sanity':runs,'denominators':{'no_fault_calibrations':2,'sanity_attempts':3,'pre_health_rejections_before_injection':1,'injected_fault_episodes':2,'repair_process_invocations':2,'actual_typed_mutations_completed':1,'reported_oracle_passes':1,'reported_oracle_nonpasses':0,'injected_without_post_oracle_due_infrastructure_failure':1,'scope':'one namespace, one service, one fault profile; different setup/code versions; no success-rate estimate'}}
(OUT/'recomputed.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'checks':len(checks),'failed':report['failed_checks'],'windows':len(windows),'raw_attempts':report['raw_attempts'],'denominators':report['denominators']},indent=2))
if report['failed_checks']:raise SystemExit(1)
