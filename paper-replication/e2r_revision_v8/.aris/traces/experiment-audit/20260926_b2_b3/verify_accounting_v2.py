from pathlib import Path
from collections import Counter
import json,math,hashlib,statistics,sys
ROOT=Path('${PROJECT_ROOT}');sys.path.insert(0,str(ROOT));BASE=ROOT/'experiments/e2r/runs';OUT=Path(__file__).resolve().parent
from rca_bench.llm_transport import parse_response
hashes={};checks=0;output={}
def load(p):
 p=Path(p);hashes[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest();return json.loads(p.read_text())
def rows(p):
 p=Path(p);hashes[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest();return [json.loads(x) for x in p.read_text().splitlines() if x]
def eq(a,b):
 global checks
 if isinstance(a,(float,int)) and isinstance(b,(float,int)):assert math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-12),(a,b)
 else:assert a==b,(a,b)
 checks+=1
for label,old,new,campaign_name in [('formal','m2_qwen38_formal_analysis_v1','m2_qwen38_formal_analysis_v2','m2_qwen38_formal_v1'),('combined','m2_qwen38_combined_analysis_v1','m2_qwen38_combined_analysis_v2','m2_qwen38_combined_campaign_v1')]:
 a=load(BASE/old/'analysis.json');b=load(BASE/new/'analysis.json');events=rows(BASE/campaign_name/'requests.jsonl');finished=[x for x in events if x['event']=='request_finished']
 for key in ('metrics','paired_comparisons','scope','status','evaluation_type'):eq(a[key],b[key])
 for key,value in a['requests'].items():eq(value,b['requests'][key])
 raw=Counter()
 for r in finished:
  for k in ('input_tokens','output_tokens','cache_creation_input_tokens','cache_read_input_tokens'):
   if isinstance(r.get('usage',{}).get(k),(int,float)):raw[k]+=r['usage'][k]
 eq(dict(raw),b['requests']['raw_usage_field_totals']);eq(b['requests']['cache_inclusive_token_volume'],sum(raw.values()))
 output[label]={'unchanged_prior_metrics_paired_scope_request_stats':True,'raw_usage':dict(raw),'cache_inclusive':sum(raw.values())}
ex=load(BASE/'m2_expenditure_v2/expenditure.json');grand=Counter();costs=[];missing=[]
for rec in ex['runs']:
 name=rec['run'];root=BASE/name;c=load(root/'campaign.json');events=rows(root/'requests.jsonl');start=[r for r in events if r['event']=='request_started'];fin=[r for r in events if r['event']=='request_finished']
 eq(len(start),len(set(r['request_id'] for r in start)));eq(len(fin),len(set(r['request_id'] for r in fin)));eq({r['request_id'] for r in start},{r['request_id'] for r in fin});eq(rec['attempts'],len(start))
 eq(rec['model_alias'],c['model_alias']);eq(rec['request_model'],c['request_model'])
 eq(rec['transport_successes'],sum(bool(r.get('transport_success')) for r in fin));eq(rec['strict_accepted'],sum(bool(r.get('validation',{}).get('accepted')) for r in fin))
 usage=Counter();raw=Counter();reported=0;localcost=[]
 for r in fin:
  if isinstance(r.get('usage'),dict):
   reported+=1
   for k in ('prompt_tokens','completion_tokens','total_tokens'):
    if isinstance(r['usage'].get(k),(int,float)):usage[k]+=r['usage'][k]
   for k in ('input_tokens','output_tokens','cache_creation_input_tokens','cache_read_input_tokens'):
    if isinstance(r['usage'].get(k),(int,float)):raw[k]+=r['usage'][k]
  else:missing.append({'campaign':name,'request_id':r['request_id']})
  if r.get('provider_response_envelope'):
   normalized=parse_response(r['provider_response_envelope'],c['api_style'])
   eq(r['usage'],normalized['usage'])
  if r.get('provider_cost') is not None:localcost.append(float(r['provider_cost']))
 eq(rec['usage_reported_attempts'],reported);eq(rec['usage'],dict(usage));eq(rec['raw_usage_fields'],dict(raw))
 eq(rec['cache_inclusive_token_volume'],usage.get('total_tokens',0)+raw.get('cache_creation_input_tokens',0)+raw.get('cache_read_input_tokens',0));eq(rec['provider_cost_reported_attempts'],len(localcost));eq(rec['provider_cost_total'],sum(localcost) if localcost else None)
 eq(rec['request_seconds_total'],sum(r['request_seconds'] for r in fin));eq(rec['request_seconds_mean'],statistics.mean(r['request_seconds'] for r in fin))
 for k in ('attempts','transport_successes','strict_accepted','usage_reported_attempts','cache_inclusive_token_volume'):grand[k]+=rec[k]
 grand.update(usage);grand.update({'raw_'+k:v for k,v in raw.items()});costs+=localcost
eq(ex['totals'],{**dict(grand),'provider_cost_reported_attempts':len(costs),'provider_cost_total':sum(costs) if costs else None})
output['expenditure']={'totals':ex['totals'],'usage_missing_attempts':missing,'note':'Totals are reported provider usage, not proof of zero usage on three failed-transport attempts.'}
for p in [ROOT/'scripts/e2r/analyze_m2_formal.py',ROOT/'scripts/e2r/summarize_m2_expenditure.py',ROOT/'scripts/e2r/combine_m2_campaigns.py',BASE/'m2_qwen38_combined_scores_v1/results.json',BASE/'m2_expenditure_v2/REPORT.md',BASE/'m2_qwen38_formal_analysis_v2/REPORT.md',BASE/'m2_qwen38_combined_analysis_v2/REPORT.md']:
 hashes[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
output['checks']=checks;(OUT/'accounting_v2_verification.json').write_text(json.dumps(output,indent=2)+'\n');(OUT/'accounting_v2_hashes.json').write_text(json.dumps(hashes,indent=2)+'\n');print(json.dumps(output,indent=2))
