"""Reviewer-authored local CPU verification; never calls a provider or mutates inputs."""
from pathlib import Path
import sys,json,hashlib,statistics,math
from collections import Counter,defaultdict
ROOT=Path('${PROJECT_ROOT}');sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True
import numpy as np
import torch
torch.set_num_threads(2)
from methods.E2R.paths import observation_file,default_dyrca_root
from methods.E2R.graph import use_upstream,operation_catalog,graphs_for,tensors,execute,select_plan,owner
use_upstream(default_dyrca_root())
from dyrca_rca.models.graphsage import WeightedGraphSAGE
from methods.E2R.llm_pilot import messages_for,validate_output,choose_cases,FORMAL_ROUTES,_forbidden_keys
from rca_bench.llm_transport import parse_response
from scripts.e2r.run_multi_root_proxy import make_pairs,merge_observations,_valid_endpoint_ip
from methods.E2R.data import build_observation
BASE=ROOT/'experiments/e2r/runs';OUT=Path(__file__).resolve().parent
hashed={};checks=Counter();report={};diffs=[]
def load(p):
    p=Path(p);hashed[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest();return json.loads(p.read_text())
def lines(p):
    p=Path(p);hashed[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest();return [json.loads(x) for x in p.read_text().splitlines() if x]
def eq(a,b,tag):
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):
        assert math.isclose(a,b,rel_tol=1e-11,abs_tol=1e-11),(tag,a,b)
    else: assert a==b,(tag,a,b)
    checks[tag]+=1
def bootstrap(v):
    v=np.asarray(v,float);ix=np.random.default_rng(20260926).integers(0,len(v),(5000,len(v)))
    return np.quantile(v[ix].mean(axis=1),[.025,.975]).tolist()
def metric(rank,m):return (0 if rank is None else (1/rank if m=='MRR' else float(rank<=int(m[-1]))))
manifest_path=BASE/'full_data_v2/manifest.json';manifest=load(manifest_path);source={r['case_id']:r for r in manifest['cases']}
split=load(BASE/'gpu_full_4090d/split.json');observations={c:load(observation_file(source[c],manifest_path)) for c in split['test']}
for c in split['test']:
    row=source[c];gt=load(Path(row['directory'])/'ground_truth.json');meta=load(Path(row['directory'])/'metadata.json')
    eq(row['true_service'],gt['root_cause_service'],'dataset_gt_service');eq(row['true_category'],gt['fault_type'],'dataset_gt_category')
    eq(row['app'],meta['environment']['namespace'],'dataset_app');eq(row['scenario'],meta['scenario_id'],'dataset_scenario')
    eq(build_observation(row['directory'],c),observations[c],'raw_snapshot_rebuild')
    assert observations[c]['window']['max_used_timestamp'] is None or observations[c]['window']['max_used_timestamp']<=observations[c]['window']['cutoff']
models={};hashes={}
for seed in (42,43,44):
    p=BASE/'gpu_full_4090d'/f'model_seed{seed}.pt';hashes[p.name]=hashlib.sha256(p.read_bytes()).hexdigest();hashed[str(p)]=hashes[p.name]
    state=torch.load(p,map_location='cpu',weights_only=True)
    eq((state['seed'],state['training_variant'],state['feature_policy']),(seed,'patch','shared_observation_v1'),'checkpoint_identity')
    model=WeightedGraphSAGE(state['in_dim'],128,2,.1);model.load_state_dict(state['state_dict']);model.eval();models[seed]=model
def replay(item,row,component):
    with torch.no_grad(): vals=models[row['seed']](item['x'],item['edge_index'],edge_weight=item['edge_weight'])[item['candidate_indices']].tolist()
    scores=dict(zip(item['candidate_services'],vals));ranking=sorted(scores,key=lambda s:(-scores[s],s))
    if ranking!=row['ranking']:
        truth=row.get('true_service');saved=row['ranking'];inversion=max([scores[b]-scores[a] for a,b in zip(saved,saved[1:])]+[0])
        change={'component':component,'case_id':row.get('case_id',row.get('pair_id')),'seed':row['seed'],'arm':row.get('arm',row.get('orientation')),'max_saved_order_inversion':inversion}
        if truth:
            rank=ranking.index(truth)+1;change.update(saved_rank=row['rank'],cpu_rank=rank,ac_changed=any((rank<=k)!=(row['rank']<=k) for k in (1,3,5)))
        else:
            roots=set(row['roots']);change.update(cpu_hits2=len(roots&set(ranking[:2])),saved_hits2=row['hits_at_2'])
        diffs.append(change)
    return ranking
def accounting(events):
    finished=[x for x in events if x['event']=='request_finished'];counts=Counter();usage=Counter();cost=[];lat=[]
    for e in finished:
        counts[e['route']+'_attempts']+=1;counts[e['route']+'_transport_success']+=bool(e.get('transport_success'));counts[e['route']+'_accepted']+=bool(e.get('validation',{}).get('accepted'))
        if e.get('usage'):
            counts['usage_reported_attempts']+=1
            for k in ('prompt_tokens','completion_tokens','total_tokens'):usage[k]+=e['usage'].get(k,0)
        if e.get('provider_cost') is not None:cost.append(float(e['provider_cost']))
        assert isinstance(e['request_seconds'],(float,int)) and e['request_seconds']>=0
        lat.append(e['request_seconds'])
    return {'attempts':len(finished),**dict(counts),'usage_missing_attempts':len(finished)-counts['usage_reported_attempts'],'usage_totals':dict(usage),'provider_cost_reported_attempts':len(cost),'provider_cost_total':sum(cost) if cost else None,'request_seconds_total':sum(lat),'request_seconds_mean':statistics.mean(lat),'request_seconds_p95':float(np.quantile(lat,.95))}
def verify_analysis(analysis,rows,campaign,events):
    selected=[r['case_id'] for r in campaign['cases']];by=defaultdict(list)
    for row in rows:by[row['case_id'],row['arm']].append(row)
    groups={'all':selected}
    for c in selected:groups.setdefault(source[c]['app'],[]).append(c)
    for group,cases in groups.items():
        ac={}
        for arm,record in analysis['metrics'][group].items():
            eq(record['incidents'],len(cases),'analysis_incidents')
            allrows=[r for c in cases for r in by[c,arm]];eq(record['ranked_row_coverage'],sum(r['status']=='ranked' for r in allrows)/len(allrows),'coverage')
            for m in ('AC@1','AC@3','AC@5','MRR'):
                v=[]
                for c in cases:
                    gens=defaultdict(list)
                    for r in by[c,arm]:gens[r.get('generation')].append(metric(r.get('rank'),m))
                    v.append(statistics.mean(statistics.mean(x) for x in gens.values()))
                eq(record[m]['mean'],statistics.mean(v),'metric_'+m)
                for a,b in zip(record[m]['incident_bootstrap_95ci'],bootstrap(v)):eq(a,b,'metric_bootstrap')
                if m=='AC@1':ac[arm]=v
        for name,rec in analysis['paired_comparisons'][group].items():
            left,right=name.split('-minus-');v=[a-b for a,b in zip(ac[left],ac[right])]
            eq(rec['n_incidents'],len(cases),'paired_n');eq(rec['AC@1_delta'],statistics.mean(v),'paired_delta')
            for a,b in zip(rec['incident_bootstrap_95ci'],bootstrap(v)):eq(a,b,'paired_bootstrap')
    expect=accounting(events)
    for k,v in expect.items():
        if isinstance(v,dict):eq(analysis['requests'][k],v,'usage_total')
        else:eq(analysis['requests'][k],v,'request_accounting')

campaign_data={};score_data={}
for tag,cn,sn,an in [('formal','m2_qwen38_formal_v1','m2_qwen38_formal_scores_v1','m2_qwen38_formal_analysis_v1'),('pilot','m2_qwen38_pilot_formal_v2','m2_qwen38_pilot_scores_v2','m2_qwen38_pilot_analysis_v2')]:
    root=BASE/cn;campaign=load(root/'campaign.json');events=lines(root/'requests.jsonl');status=load(root/'status.json');obs=load(root/'frozen_inputs.json');labels=load(root/'evaluator_labels.json')
    selected=[r['case_id'] for r in campaign['cases']];eq(set(obs),set(selected),'frozen_input_identity');eq(set(labels),set(selected),'label_identity')
    assert set(selected)<=set(split['test']) and not set(selected)&set(split['train']+split['val'])
    eligible=[source[c] for c in split['test'] if c not in campaign['excluded_case_ids']]
    eq([r['case_id'] for r in choose_cases(eligible,observations,len(selected),campaign['selection_seed'])],selected,'case_selection')
    for c in selected:
        eq(obs[c],observations[c],'frozen_exact_snapshot');eq(labels[c],{k:source[c][k] for k in ('app','scenario','true_service')},'labels_source_exact')
    start={};finish={}
    for e in events:
        assert e['event'] in ('request_started','request_finished');table=start if e['event']=='request_started' else finish
        assert e['request_id'] not in table;table[e['request_id']]=e
        if e['event']=='request_finished':assert e['request_id'] in start
    expected={f'{c}-g{g}-{route}' for c in selected for g in range(2) for route in FORMAL_ROUTES}
    eq(set(start),expected,'request_starts_exact');eq(set(finish),expected,'request_finishes_exact');eq(len(start),campaign['authorized_request_ceiling'],'campaign_ceiling')
    errors=Counter();stop_reasons=Counter();provider_ids=[]
    for number,(request_id,s) in enumerate(start.items(),1):
        f=finish[request_id];c=s['case_id'];route=s['route'];g=s['generation']
        eq(number,s['attempt_number'],'attempt_sequence');eq(request_id,f'{c}-g{g}-{route}','request_identity')
        eq({k:s[k] for k in ('case_id','generation','route')},{k:f[k] for k in ('case_id','generation','route')},'request_pair_metadata')
        messages,catalog=messages_for(obs[c],route);eq(messages,s['messages'],'frozen_prompt');eq(s['actual_request_settings'],campaign['actual_request_settings'],'request_settings')
        assert not _forbidden_keys(json.loads(messages[1]['content']))
        assert source[c]['original_id'] not in messages[1]['content']
        if f['transport_success']:
            envelope=f['provider_response_envelope'];normalized=parse_response(envelope,campaign['api_style'])
            for k,v in normalized.items():eq(f[k],v,'provider_envelope_'+k)
            validation=validate_output(f['response_text'],route,obs[c],catalog);eq(validation,f['validation'],'response_validation')
            errors.update(validation['errors']);stop_reasons[envelope.get('stop_reason')]+=1;provider_ids.append(f.get('provider_request_id'))
    eq(len(provider_ids),len(set(provider_ids)),'provider_ids_unique')
    rows=lines(BASE/sn/'predictions.jsonl');result=load(BASE/sn/'results.json');rowdict={}
    for r in rows:
        ident=(r['case_id'],r['arm'],r.get('generation'),r.get('seed'));assert ident not in rowdict;rowdict[ident]=r
        eq({k:r[k] for k in ('app','scenario','true_service')},labels[r['case_id']],'prediction_label')
        if r['status']=='ranked':
            eq(set(r['ranking']),set(obs[r['case_id']]['candidates']),'candidate_permutation_set');eq(len(r['ranking']),len(set(r['ranking'])),'candidate_permutation_unique');eq(r['rank'],r['ranking'].index(r['true_service'])+1,'rank_from_ranking')
        else:eq(r['rank'],None,'failed_zero_rank')
    expected_rows=set()
    for c in selected:
        for arm in ('reference','runtime','rule_patch'):
            for seed in (42,43,44):expected_rows.add((c,arm,None,seed))
        for g in range(2):
            for arm in ('proposal_rank_no_gnn','raw_direct_rank','catalog_direct_rank'):expected_rows.add((c,arm,g,None))
            for arm in ('llm_patch_no_fallback','llm_patch_with_fallback'):
                for seed in (42,43,44):expected_rows.add((c,arm,g,seed))
    eq(set(rowdict),expected_rows,'prediction_product_exact');eq(result['row_status'],dict(Counter(r['status'] for r in rows)),'saved_row_counts')
    if 'checkpoint_sha256' in result:eq(result['checkpoint_sha256'],hashes,'frozen_hashes')
    for c in selected:
        gs,cat,_=graphs_for(obs[c]);items={name:tensors(graph,obs[c]) for name,graph in gs.items()}
        for arm,var in [('reference','reference'),('runtime','runtime'),('rule_patch','patch')]:
            for seed in (42,43,44):replay(items[var],rowdict[c,arm,None,seed],tag)
        for g in range(2):
            for route,arm in [('proposal','proposal_rank_no_gnn'),('raw_direct','raw_direct_rank'),('catalog_direct','catalog_direct_rank')]:
                event=finish[f'{c}-g{g}-{route}'];v=event.get('validation',{});r=rowdict[c,arm,g,None]
                eq(r['status']=='ranked',bool(v.get('accepted')),'direct_acceptance')
                if v.get('accepted'):eq(r['ranking'],v['ranking'],'direct_exact_response')
            event=finish[f'{c}-g{g}-proposal'];v=event.get('validation',{});accepted=bool(v.get('accepted'))
            item=tensors(execute(obs[c],select_plan(obs[c],cat,v['selected_operation_ids'])),obs[c]) if accepted else items['patch']
            for seed in (42,43,44):
                a=rowdict[c,'llm_patch_no_fallback',g,seed];b=rowdict[c,'llm_patch_with_fallback',g,seed]
                eq(a['status']=='ranked',accepted,'llm_failed_retained');eq(b['fallback_used'],not accepted,'fallback_status')
                if accepted:eq(a['ranking'],b['ranking'],'accepted_fallback_same')
                else:eq(b['ranking'],rowdict[c,'rule_patch',None,seed]['ranking'],'fallback_equals_rule')
                replay(item,b,tag)
    analysis=load(BASE/an/'analysis.json');verify_analysis(analysis,rows,campaign,events)
    report[tag]={'cases':len(selected),'rows':len(rows),'requests':accounting(events),'row_status':result['row_status'],'validation_errors':dict(errors),'provider_stop_reasons':dict(stop_reasons),'all_metrics':analysis['metrics']['all'],'paired_all':analysis['paired_comparisons']['all'],'checkpoint_hash_recorded':'checkpoint_sha256' in result}
    campaign_data[tag]=(campaign,events,obs,labels,status);score_data[tag]=rows
    print(tag,'verified',len(rows),'rows',flush=True)

combined=load(BASE/'m2_qwen38_combined_campaign_v1/campaign.json');cevents=lines(BASE/'m2_qwen38_combined_campaign_v1/requests.jsonl');crows=lines(BASE/'m2_qwen38_combined_scores_v1/predictions.jsonl')
eq(cevents,campaign_data['pilot'][1]+campaign_data['formal'][1],'combined_exact_journal_concat');eq(crows,score_data['pilot']+score_data['formal'],'combined_exact_prediction_concat')
eq(set(r['case_id'] for r in combined['cases']),set(split['test']),'combined_covers_original_test')
eq(set(campaign_data['pilot'][2])&set(campaign_data['formal'][2]),set(),'pilot_formal_disjoint')
for index,name in [(2,'frozen_inputs.json'),(3,'evaluator_labels.json')]:
    eq(load(BASE/'m2_qwen38_combined_campaign_v1'/name),{**campaign_data['pilot'][index],**campaign_data['formal'][index]},'combined_'+name)
ca=load(BASE/'m2_qwen38_combined_analysis_v1/analysis.json');verify_analysis(ca,crows,combined,cevents)
report['combined']={'cases':116,'rows':len(crows),'requests':accounting(cevents),'row_status':dict(Counter(r['status'] for r in crows)),'all_metrics':ca['metrics']['all'],'paired_all':ca['paired_comparisons']['all']}
old=lines(BASE/'m2_qwen38_pilot_formal_v1/requests.jsonl');report['failed_pilot_v1_accounting']=accounting(old)
report['qwen_total_expenditure_attempts_including_failed_pilot']=len(cevents)//2+len(old)//2

# All multi-root compositions and predictions.
proxy=BASE/'multi_root_proxy_v1';pr=load(proxy/'results.json');pair_file=load(proxy/'pairs.json');prows=lines(proxy/'predictions.jsonl')
grouped=defaultdict(list)
for c in split['test']:grouped[source[c]['app']].append(source[c])
pairs=[];leftovers=[]
for offset,app in enumerate(sorted(grouped)):
    pp,ll=make_pairs(grouped[app],pair_file['seed']+offset);pairs+=pp;leftovers+=ll
eq(pair_file['leftovers'],[r['case_id'] for r in leftovers],'pair_leftovers')
ids=[r['case_id'] for p in pairs for r in p];eq(len(ids),len(set(ids)),'pairs_disjoint');eq(set(ids)|{r['case_id'] for r in leftovers},set(split['test']),'pair_coverage')
lookup={(r['pair_id'],r['orientation'],r['seed']):r for r in prows};eq(len(lookup),len(prows),'proxy_rows_unique')
eq(set(lookup),{(f'pair{i:03d}',o,s) for i in range(len(pairs)) for o in ('left_base','right_base') for s in (42,43,44)},'proxy_exact_product')
eq(pr['checkpoint_sha256'],{str(s):hashes[f'model_seed{s}.pt'] for s in (42,43,44)},'proxy_hashes')
proxy_diag=Counter();imported_stable=0
for i,(left,right) in enumerate(pairs):
    pid=f'pair{i:03d}';record=pair_file['pairs'][i]
    eq((record['left_case_id'],record['right_case_id']),(left['case_id'],right['case_id']),'pair_generation_exact')
    assert left['app']==right['app'] and left['true_service']!=right['true_service']
    roots={left['true_service'],right['true_service']}
    for orientation,base_row,overlay_row in [('left_base',left,right),('right_base',right,left)]:
        base=observations[base_row['case_id']];overlay=observations[overlay_row['case_id']];root=overlay_row['true_service'];sid=pid+':'+orientation
        obs=merge_observations(base,overlay,root,sid);eq(obs,merge_observations(base,overlay,root,sid),'composition_deterministic')
        eq(obs['window']['base_source_window'],base['window'],'time_base');eq(obs['window']['overlay_source_window'],overlay['window'],'time_overlay')
        assert obs['window']['synthetic'] and obs['synthetic_composition']['construction_uses_evaluator_root_to_select_overlay_service']
        assert not _forbidden_keys(obs)
        for phase in ('pre','during'):
            phase_obs=obs[phase];names=[p['name'] for p in phase_obs['pods']];eq(len(names),len(set(names)),'pod_names_unique')
            ip_rows=defaultdict(list)
            for pod in phase_obs['pods']:
                if _valid_endpoint_ip(pod.get('ip')):ip_rows[pod['ip']].append(pod)
            assert max(map(len,ip_rows.values()),default=0)<=1
            checks['pod_ips_unique']+=1
            for svc in phase_obs['services']:
                for ip in svc.get('endpoint_ips',[]):assert len(ip_rows[ip])==1
            expected_edges={}
            for edge in base['call_edges'][phase]+overlay['call_edges'][phase]:
                key=(edge['source'],edge['target']);expected_edges[key]=max(expected_edges.get(key,0),int(edge.get('count',0) or 0))
            eq({(e['source'],e['target']):e.get('count',0) for e in obs['call_edges'][phase]},expected_edges,'relation_union_max')
            assert len(expected_edges)==len(obs['call_edges'][phase])
            proxy_diag.update(obs['synthetic_composition']['phase_diagnostics'][phase])
        pre={p['name']:p for p in overlay['pre']['pods']};during={p['name']:p for p in overlay['during']['pods']}
        for name in set(pre)&set(during):
            if owner(pre[name].get('service'),set(obs['candidates']))!=root or owner(during[name].get('service'),set(obs['candidates']))!=root:continue
            key=hashlib.sha256(f'{sid}:pod:{name}'.encode()).hexdigest()[:10];mapped=name+'__mr_'+key
            a=next(p for p in obs['pre']['pods'] if p['name']==mapped);b=next(p for p in obs['during']['pods'] if p['name']==mapped)
            if pre[name].get('ip')==during[name].get('ip'):eq(a.get('ip'),b.get('ip'),'stable_unchanged_pod_ip')
            imported_stable+=1
        graph=graphs_for(obs)[0]['patch'];item=tensors(graph,obs)
        for seed in (42,43,44):
            row=lookup[pid,orientation,seed];eq(set(row['roots']),roots,'proxy_gt');eq(set(row['ranking']),set(obs['candidates']),'proxy_candidate_set');eq(len(row['ranking']),len(set(row['ranking'])),'proxy_permutation')
            for k in (1,2,5):eq(row[f'hits_at_{k}'],len(roots&set(row['ranking'][:k])),'proxy_hits')
            eq(row['exact_set_at_2'],set(row['ranking'][:2])==roots,'proxy_exact_set')
            positions=sorted(row['ranking'].index(r)+1 for r in roots);ap=(1/positions[0]+2/positions[1])/2;eq(row['average_precision'],ap,'proxy_ap')
            replay(item,row,'proxy')
for key,summary in pr['summary'].items():
    seed,o=key.split(':');rows=[r for r in prows if r['seed']==int(seed) and r['orientation']==o];n=len(rows);eq(summary['n'],n,'proxy_summary_n')
    for k in (1,2,5):eq(summary[f'Recall@{k}'],sum(r[f'hits_at_{k}'] for r in rows)/(2*n),'proxy_summary_recall')
    eq(summary['ExactSet@2'],sum(r['exact_set_at_2'] for r in rows)/n,'proxy_summary_exact');eq(summary['MAP'],statistics.mean(r['average_precision'] for r in rows),'proxy_summary_map')
report['proxy']={'source_incidents':len(split['test']),'pairs':len(pairs),'compositions':2*len(pairs),'rows':len(prows),'leftovers':pair_file['leftovers'],'phase_diagnostics_total':dict(proxy_diag),'stable_cross_phase_imported_pods':imported_stable,'summary':pr['summary']}
report['cpu_replay']={'torch':torch.__version__,'device':'cpu','ranking_differences':len(diffs),'by_component':dict(Counter(d['component'] for d in diffs)),'true_rank_differences':sum(d.get('saved_rank')!=d.get('cpu_rank') for d in diffs if 'saved_rank' in d),'ac_differences':sum(d.get('ac_changed',False) for d in diffs),'proxy_hits2_differences':sum(d.get('cpu_hits2')!=d.get('saved_hits2') for d in diffs if 'cpu_hits2' in d),'max_saved_order_score_inversion':max([d['max_saved_order_inversion'] for d in diffs]+[0])}
report['check_counts']=dict(checks)
(OUT/'qwen_proxy_verification.json').write_text(json.dumps(report,indent=2)+'\n');(OUT/'cpu_replay_differences.json').write_text(json.dumps(diffs,indent=2)+'\n');(OUT/'qwen_proxy_hashes.json').write_text(json.dumps(hashed,indent=2)+'\n')
print(json.dumps({'checks':sum(checks.values()),'formal_rows':report['formal']['rows'],'combined_rows':len(crows),'proxy':report['proxy'],'cpu_replay':report['cpu_replay']},indent=2))
