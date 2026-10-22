#!/usr/bin/env python3
"""Summarize all immutable M2 provider attempts, including failed pilots."""
from __future__ import annotations

import argparse,json,statistics
from collections import Counter
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs',nargs='+',required=True);parser.add_argument('--out-dir',required=True)
    args=parser.parse_args();summaries=[];grand=Counter();all_costs=[]
    for value in args.runs:
        root=Path(value);campaign=json.loads((root/'campaign.json').read_text())
        events=[json.loads(line) for line in (root/'requests.jsonl').read_text().splitlines() if line]
        started=[row for row in events if row['event']=='request_started']
        finished=[row for row in events if row['event']=='request_finished']
        if len(started)!=len(finished) or {x['request_id'] for x in started}!={x['request_id'] for x in finished}:
            raise ValueError(f'unknown request outcome in {root}')
        usage=Counter();raw_usage=Counter();usage_rows=0;costs=[];seconds=[]
        for row in finished:
            if isinstance(row.get('usage'),dict):
                usage_rows+=1
                for key in ('prompt_tokens','completion_tokens','total_tokens'):
                    if isinstance(row['usage'].get(key),(int,float)):usage[key]+=row['usage'][key]
                for key in ('input_tokens','output_tokens','cache_creation_input_tokens','cache_read_input_tokens'):
                    if isinstance(row['usage'].get(key),(int,float)):raw_usage[key]+=row['usage'][key]
            if row.get('provider_cost') is not None:
                try:costs.append(float(row['provider_cost']))
                except (TypeError,ValueError):pass
            if isinstance(row.get('request_seconds'),(int,float)):seconds.append(row['request_seconds'])
        row={'run':root.name,'model_alias':campaign['model_alias'],'request_model':campaign['request_model'],
             'attempts':len(started),'transport_successes':sum(bool(x.get('transport_success')) for x in finished),
             'strict_accepted':sum(bool(x.get('validation',{}).get('accepted')) for x in finished),
             'usage_reported_attempts':usage_rows,'usage':dict(usage),'raw_usage_fields':dict(raw_usage),
             'cache_inclusive_token_volume':(usage.get('total_tokens',0)+raw_usage.get('cache_creation_input_tokens',0)+raw_usage.get('cache_read_input_tokens',0)) if usage_rows else None,
             'provider_cost_reported_attempts':len(costs),'provider_cost_total':sum(costs) if costs else None,
             'request_seconds_total':sum(seconds),'request_seconds_mean':statistics.mean(seconds) if seconds else None}
        summaries.append(row);grand['attempts']+=row['attempts'];grand['transport_successes']+=row['transport_successes'];grand['strict_accepted']+=row['strict_accepted'];grand['usage_reported_attempts']+=usage_rows
        for key,val in usage.items():grand[key]+=val
        for key,val in raw_usage.items():grand['raw_'+key]+=val
        if row['cache_inclusive_token_volume'] is not None:
            grand['cache_inclusive_reported_volume']+=row['cache_inclusive_token_volume']
        all_costs+=costs
    output={'status':'completed_m2_expenditure_accounting','runs':summaries,
            'totals':{**dict(grand),'provider_cost_reported_attempts':len(all_costs),
                      'provider_cost_total':sum(all_costs) if all_costs else None},
            'limits':['Provider cost is null when the gateway did not report a cost; no alias-based price is fabricated.',
                      'Cache-inclusive token volume adds cache creation/read fields to normalized total_tokens and is not a monetary bill.',
                      'A failed attempt with missing usage is unknown usage, not zero; aggregate token volumes sum reported rows only.',
                      'Failed transports and the stopped Qwen v1 compatibility pilot remain in attempt totals.']}
    out=Path(args.out_dir)
    if out.exists():raise SystemExit('Refusing overwrite')
    out.mkdir(parents=True);(out/'expenditure.json').write_text(json.dumps(output,indent=2)+'\n')
    lines=['# E2R M2 request and token accounting','',
           '| Run | Model | Attempts | Transport | Strict valid | Usage rows | Normalized tokens | Cache-inclusive volume | Request seconds | Provider cost |',
           '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for row in summaries:
        lines.append(f"| {row['run']} | {row['request_model']} | {row['attempts']} | {row['transport_successes']} | {row['strict_accepted']} | {row['usage_reported_attempts']} | {row['usage'].get('total_tokens','—') if row['usage_reported_attempts'] else 'unknown'} | {row['cache_inclusive_token_volume'] if row['cache_inclusive_token_volume'] is not None else 'unknown'} | {row['request_seconds_total']:.2f} | {row['provider_cost_total'] if row['provider_cost_total'] is not None else 'not reported'} |")
    totals=output['totals'];lines+=['',f"Total attempts: {totals['attempts']}; normalized input+output tokens on reported rows: {totals.get('total_tokens',0)}; cache-inclusive reported volume: {totals.get('cache_inclusive_reported_volume',0)}; provider cost: {totals['provider_cost_total'] if totals['provider_cost_total'] is not None else 'not reported'}.",'',*['- '+item for item in output['limits']]]
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n');print(json.dumps(output['totals'],indent=2))


if __name__=='__main__':main()
