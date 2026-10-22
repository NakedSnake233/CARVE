from pathlib import Path
import json,hashlib,math,statistics
from collections import Counter,defaultdict
from datetime import datetime
ROOT=Path('${PROJECT_ROOT}');BASE=ROOT/'experiments/e2r/runs';OUT=Path(__file__).resolve().parent
report={};hashes={};totals=Counter()
for tag,name in [('failed_pilot_v1','m2_qwen38_pilot_formal_v1'),('pilot_v2','m2_qwen38_pilot_formal_v2'),('formal','m2_qwen38_formal_v1'),('combined','m2_qwen38_combined_campaign_v1')]:
    root=BASE/name;events=[json.loads(x) for x in (root/'requests.jsonl').read_text().splitlines()];status=json.loads((root/'status.json').read_text());campaign=json.loads((root/'campaign.json').read_text())
    starts={r['request_id']:r for r in events if r['event']=='request_started'};finishes={r['request_id']:r for r in events if r['event']=='request_finished'}
    assert len(events)==len(starts)+len(finishes) and starts.keys()==finishes.keys()
    values={'authorized_request_ceiling':campaign['authorized_request_ceiling'],'actual_http_attempts_started':len(starts),'finished_attempts':len(finishes),'unknown_outcome_attempts':[],'transport_successes':sum(bool(r['transport_success']) for r in finishes.values()),'strict_accepted':sum(bool(r.get('validation',{}).get('accepted')) for r in finishes.values()),'response_models':sorted({r['response_model'] for r in finishes.values() if r.get('response_model')}),'provider_usage_missing_attempts':sum(r.get('usage') is None for r in finishes.values())}
    for k,v in values.items():assert status[k]==v,(name,k)
    usage=Counter();timestamps=[]
    for rid,r in finishes.items():
        t0=datetime.fromisoformat(starts[rid]['time_utc'].replace('Z','+00:00'));t1=datetime.fromisoformat(r['time_utc'].replace('Z','+00:00'))
        assert t1>=t0 and abs((t1-t0).total_seconds()-r['request_seconds'])<2
        for k,v in r.get('usage',{}).items():
            if isinstance(v,(int,float)):usage[k]+=v
        if r.get('transport_success') and r.get('provider_response_envelope'):
            assert r['usage']['input_tokens']==r['provider_response_envelope']['usage']['input_tokens']
            assert r['usage']['cache_read_input_tokens']==r['provider_response_envelope']['usage']['cache_read_input_tokens']
    report[tag]={'status_consistent':True,'journal_timestamp_seconds_consistent':True,'attempts':len(starts),'raw_provider_envelopes':sum(bool(r.get('provider_response_envelope')) for r in finishes.values()),'all_numeric_usage_fields':dict(usage),'cache_inclusive_input_and_output_tokens':usage['input_tokens']+usage['cache_read_input_tokens']+usage['cache_creation_input_tokens']+usage['output_tokens']}
    if tag!='combined':totals.update(usage)
report['all_qwen_expenditure']={'attempts':699,'all_numeric_usage_fields':dict(totals),'cache_inclusive_input_and_output_tokens':totals['input_tokens']+totals['cache_read_input_tokens']+totals['cache_creation_input_tokens']+totals['output_tokens'],'monetary_cost':None,'note':'Cache-inclusive volume is accounting only; token classes may have different billing rules. No provider monetary cost was returned.'}
rows=[json.loads(x) for x in (BASE/'m2_qwen38_combined_scores_v1/predictions.jsonl').read_text().splitlines()];timings=defaultdict(list)
for r in rows:
    if 'inference_seconds' in r:
        assert isinstance(r['inference_seconds'],(float,int)) and math.isfinite(r['inference_seconds']) and r['inference_seconds']>=0
        timings[r['arm']].append(r['inference_seconds'])
report['gnn_timing_rows']={a:{'n':len(v),'mean':statistics.mean(v),'min':min(v),'max':max(v)} for a,v in timings.items()}
manifest=json.loads((BASE/'full_data_v2/manifest.json').read_text());source={r['case_id']:r for r in manifest['cases']};split=json.loads((BASE/'gpu_full_4090d/split.json').read_text())
files=[]
for c in split['test']:
    d=Path(source[c]['directory']);files.extend(d/'topology'/x for x in ['pre.json','during.json']);files.extend((d/'metrics').glob('*.csv'));files.extend([d/'traces/trace_summary.csv',d/'logs/all.jsonl'])
files.extend(ROOT/p for p in ['methods/E2R/data.py','methods/E2R/graph.py','methods/E2R/paths.py','methods/E2R/llm_pilot.py','rca_bench/llm_transport.py','scripts/e2r/run_m2_formal.py','scripts/e2r/score_m2_formal.py','scripts/e2r/analyze_m2_formal.py','scripts/e2r/combine_m2_campaigns.py','scripts/e2r/run_multi_root_proxy.py','scripts/e2r/run_graph_experiment.py','refine-logs/EXPERIMENT_TRACKER.md','refine-logs/EXPERIMENT_PLAN.md'])
for name in ['m2_qwen38_formal_v1','m2_qwen38_formal_scores_v1','m2_qwen38_formal_analysis_v1','m2_qwen38_pilot_formal_v1','m2_qwen38_pilot_formal_v2','m2_qwen38_pilot_scores_v2','m2_qwen38_pilot_analysis_v2','m2_qwen38_combined_campaign_v1','m2_qwen38_combined_scores_v1','m2_qwen38_combined_analysis_v1','multi_root_proxy_v1']:
    files.extend(p for p in (BASE/name).glob('*') if p.is_file() and p.suffix in ['.json','.jsonl','.md'])
for p in files:
    if p.is_file():hashes[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
(OUT/'qwen_supplement_verification.json').write_text(json.dumps(report,indent=2)+'\n');(OUT/'qwen_supplement_hashes.json').write_text(json.dumps(hashes,indent=2)+'\n')
print(json.dumps(report,indent=2))
