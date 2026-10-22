#!/usr/bin/env python3
"""Analyze a reviewer-aligned M2 campaign with incident-blocked unconditional metrics."""
from pathlib import Path
from collections import Counter,defaultdict
import argparse,json,statistics
import numpy as np

METRICS={'AC@1':lambda r:float(r is not None and r<=1),'AC@3':lambda r:float(r is not None and r<=3),
         'AC@5':lambda r:float(r is not None and r<=5),'MRR':lambda r:0.0 if r is None else 1/r}
ARMS=('reference','runtime','rule_patch','llm_patch_no_fallback','llm_patch_with_fallback',
      'proposal_rank_no_gnn','raw_direct_rank','catalog_direct_rank')


def unit(rows,metric):
    by_generation=defaultdict(list);fn=METRICS[metric]
    for row in rows:by_generation[row.get('generation')].append(fn(row.get('rank')))
    return statistics.mean(statistics.mean(values) for values in by_generation.values())


def ci(values):
    x=np.asarray(values,float);rng=np.random.default_rng(20260926);draw=rng.integers(0,len(x),(5000,len(x)))
    return np.quantile(x[draw].mean(axis=1),[.025,.975]).tolist()


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--score-dir',required=True)
    ap.add_argument('--campaign-dir',required=True);ap.add_argument('--out-dir',required=True);args=ap.parse_args()
    score=Path(args.score_dir);campaign_dir=Path(args.campaign_dir);result=json.loads((score/'results.json').read_text())
    campaign=json.loads((campaign_dir/'campaign.json').read_text());rows=[json.loads(x) for x in (score/'predictions.jsonl').read_text().splitlines()]
    if result['status']!='completed_m2_formal_scoring' or result['cases']!=len(campaign['cases']):raise ValueError('scope mismatch')
    selected=[x['case_id'] for x in campaign['cases']];generations=campaign['generations'];identities=set();by=defaultdict(list)
    for row in rows:
        ident=(row['case_id'],row['arm'],row.get('generation'),row.get('seed'))
        if ident in identities:raise ValueError(f'duplicate prediction identity {ident}')
        identities.add(ident);by[row['case_id'],row['arm']].append(row)
        if row['status']=='ranked' and row['rank']!=row['ranking'].index(row['true_service'])+1:raise ValueError('rank mismatch')
    expected=set()
    for case in selected:
        for arm in ('reference','runtime','rule_patch'):expected|={(case,arm,None,s) for s in (42,43,44)}
        for g in range(generations):
            expected|={(case,arm,g,None) for arm in ('proposal_rank_no_gnn','raw_direct_rank','catalog_direct_rank')}
            expected|={(case,arm,g,s) for arm in ('llm_patch_no_fallback','llm_patch_with_fallback') for s in (42,43,44)}
    if identities!=expected:raise ValueError('exact prediction identity coverage mismatch')
    app={row['case_id']:row['app'] for row in rows};groups={'all':selected}
    for case in selected:groups.setdefault(app[case],[]).append(case)
    metrics={};per_case={}
    for group,cases in groups.items():
        metrics[group]={}
        for arm in ARMS:
            values={m:[unit(by[c,arm],m) for c in cases] for m in METRICS}
            metrics[group][arm]={'incidents':len(cases),'ranked_row_coverage':sum(r['status']=='ranked' for c in cases for r in by[c,arm])/sum(len(by[c,arm]) for c in cases),
                                 **{m:{'mean':float(np.mean(v)),'incident_bootstrap_95ci':ci(v)} for m,v in values.items()}}
            for case,value in zip(cases,values['AC@1']):per_case.setdefault(group,{}).setdefault(case,{})[arm]=value
    contrasts={}
    pairs=[('llm_patch_no_fallback','catalog_direct_rank'),('llm_patch_no_fallback','rule_patch'),
           ('catalog_direct_rank','raw_direct_rank'),('llm_patch_no_fallback','proposal_rank_no_gnn')]
    for group,cases in groups.items():
        contrasts[group]={}
        for left,right in pairs:
            values=[per_case[group][c][left]-per_case[group][c][right] for c in cases]
            contrasts[group][f'{left}-minus-{right}']={'AC@1_delta':float(np.mean(values)),'incident_bootstrap_95ci':ci(values),'n_incidents':len(cases)}
    events=[json.loads(x) for x in (campaign_dir/'requests.jsonl').read_text().splitlines()]
    finished=[x for x in events if x['event']=='request_finished'];attempts=[x for x in events if x['event']=='request_started']
    if len(attempts)!=campaign['authorized_request_ceiling'] or len(finished)!=len(attempts):raise ValueError('request coverage mismatch')
    counts=Counter();usage=Counter();raw_usage=Counter();costs=[];latency=[]
    for e in finished:
        counts[f"{e['route']}_attempts"]+=1;counts[f"{e['route']}_transport_success"]+=int(bool(e.get('transport_success')))
        v=e.get('validation',{});counts[f"{e['route']}_accepted"]+=int(bool(v.get('accepted')))
        if e.get('usage'):
            counts['usage_reported_attempts']+=1
            for key in ('prompt_tokens','completion_tokens','total_tokens'):
                if isinstance(e['usage'].get(key),(int,float)):usage[key]+=e['usage'][key]
            for key in ('input_tokens','output_tokens','cache_creation_input_tokens','cache_read_input_tokens'):
                if isinstance(e['usage'].get(key),(int,float)):raw_usage[key]+=e['usage'][key]
        try:
            if e.get('provider_cost') is not None:costs.append(float(e['provider_cost']))
        except (TypeError,ValueError):counts['invalid_provider_cost']+=1
        latency.append(e['request_seconds'])
    requests={'attempts':len(attempts),**dict(counts),'usage_missing_attempts':len(finished)-counts['usage_reported_attempts'],
              'usage_totals':dict(usage),'raw_usage_field_totals':dict(raw_usage),
              'cache_inclusive_token_volume':usage.get('total_tokens',0)+raw_usage.get('cache_creation_input_tokens',0)+raw_usage.get('cache_read_input_tokens',0),
              'provider_cost_reported_attempts':len(costs),'provider_cost_total':sum(costs) if costs else None,
              'request_seconds_total':sum(latency),'request_seconds_mean':statistics.mean(latency),'request_seconds_p95':float(np.quantile(latency,.95))}
    output={'status':'completed_m2_formal_analysis','evaluation_type':'real_gt','scope':{'incidents':len(selected),'generations':generations,
            'model_alias':campaign['model_alias'],'request_model':campaign['request_model'],'actual_request_settings':campaign['actual_request_settings'],'seeds':[42,43,44]},
            'requests':requests,'metrics':metrics,'paired_comparisons':contrasts,
            'limits':['Failed/invalid/abstained outputs remain in unconditional denominators as zero.','GNN arms use frozen patch-trained M1 scorers.',
                      'proposal_rank_no_gnn is the proposal response ranking, not an implemented evidence reranker.',
                      'Cache-inclusive token volume is usage volume, not a monetary bill; provider cost remains missing unless explicitly returned.',
                      'No recovery or production claim.']}
    out=Path(args.out_dir)
    if out.exists():raise SystemExit('Refusing overwrite')
    out.mkdir(parents=True);(out/'analysis.json').write_text(json.dumps(output,indent=2)+'\n')
    lines=['# E2R M2 matched-information LLM analysis','',f"Model: `{campaign['request_model']}`; incidents={len(selected)}; generations={generations}.",'',
           '| Group | Arm | Coverage | AC@1 | AC@3 | AC@5 | MRR |','|---|---|---:|---:|---:|---:|---:|']
    for group,arms in metrics.items():
        for arm,value in arms.items():lines.append('| '+' | '.join([group,arm,f"{value['ranked_row_coverage']:.3f}",*[f"{value[m]['mean']:.4f}" for m in METRICS]])+' |')
    lines+=['','## Request accounting','',f"- Attempts: {requests['attempts']}/{campaign['authorized_request_ceiling']}",
            f"- Usage reported: {requests.get('usage_reported_attempts',0)}/{len(finished)}",
            f"- Normalized input+output tokens: {requests['usage_totals'].get('total_tokens',0)}",
            f"- Cache-inclusive token volume: {requests['cache_inclusive_token_volume']}",
            f"- Provider cost total: {requests['provider_cost_total']}",'','## Limits','',*['- '+x for x in output['limits']]]
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n');print(json.dumps(requests,indent=2))


if __name__=='__main__':main()
