#!/usr/bin/env python3
"""Combine audited patch M1 with matched reference/runtime/node-only training runs."""
from pathlib import Path
from collections import defaultdict
import argparse,json,statistics
import numpy as np

METRICS={'AC@1':lambda r:float(r<=1),'AC@3':lambda r:float(r<=3),'AC@5':lambda r:float(r<=5),'MRR':lambda r:1/r}


def load(directory,train):
    directory=Path(directory);result=json.loads((directory/'results.json').read_text())
    if result['training_variant']!=train or result['seeds']!=[42,43,44]:raise ValueError(f'run identity mismatch: {directory}')
    split=json.loads((directory/'split.json').read_text());pred=[json.loads(x) for x in (directory/'predictions.jsonl').read_text().splitlines()]
    return result,split,pred


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for name in ('reference','runtime','patch','node_only'):ap.add_argument(f'--{name}',required=True)
    ap.add_argument('--manifest',default='experiments/e2r/runs/full_data_v2/manifest.json')
    ap.add_argument('--input-audit',default='experiments/e2r/runs/input_audit_v1/cases.jsonl')
    ap.add_argument('--out-dir',required=True);args=ap.parse_args();runs={}
    split0=None
    for train in ('reference','runtime','patch','node_only'):
        result,split,pred=load(getattr(args,train),train)
        if split0 is None:split0=split
        elif split!=split0:raise ValueError('split mismatch across training conditions')
        runs[train]=(result,pred)
    index={}
    for train,(result,rows) in runs.items():
        for row in rows:
            key=(train,row['seed'],row['case_id'],row['variant'])
            if key in index:raise ValueError('duplicate prediction')
            index[key]=row
    manifest={r['case_id']:r for r in json.load(open(args.manifest))['cases']}
    audit={r['case_id']:r for r in map(json.loads,Path(args.input_audit).read_text().splitlines())}
    test=split0['test'];matrix={};per_incident={}
    for train in ('reference','runtime','patch','node_only'):
        evals=runs[train][0].get('evaluation_variants',['reference','runtime','patch'])
        matrix[train]={}
        for variant in evals:
            seed_values={}
            for seed in (42,43,44):
                group=[index[train,seed,c,variant] for c in test]
                seed_values[str(seed)]={m:statistics.mean(fn(r['rank']) for r in group) for m,fn in METRICS.items()}
            matrix[train][variant]={'by_seed':seed_values,
                'mean':{m:statistics.mean(v[m] for v in seed_values.values()) for m in METRICS},
                'sample_std':{m:statistics.stdev(v[m] for v in seed_values.values()) for m in METRICS}}
            for case in test:
                per_incident.setdefault(case,{})[f'{train}->{variant}']=statistics.mean(
                    METRICS['AC@1'](index[train,seed,case,variant]['rank']) for seed in (42,43,44))
    contrasts={};raw_deltas={}
    for left,right in [('patch->patch','runtime->runtime'),('patch->patch','reference->reference'),('patch->patch','node_only->node_only')]:
        values=np.array([per_incident[c][left]-per_incident[c][right] for c in test]);rng=np.random.default_rng(20260926)
        boot=values[rng.integers(0,len(values),(5000,len(values)))].mean(axis=1)
        conditions=defaultdict(list)
        for case,value in zip(test,values):
            row=manifest[case];conditions[(row['app'],row['scenario'],row['true_service'],row['true_category'])].append(value)
        condition_values=np.array([np.mean(v) for v in conditions.values()]);crng=np.random.default_rng(20260926)
        cboot=condition_values[crng.integers(0,len(condition_values),(5000,len(condition_values)))].mean(axis=1)
        signs=rng.choice([-1,1],size=(10000,len(values)));permuted=(signs*values).mean(axis=1)
        p=float((np.sum(np.abs(permuted)>=abs(values.mean()))+1)/(len(permuted)+1))
        key=f'{left}-minus-{right}';raw_deltas[key]=values
        contrasts[key]={'AC@1_delta':float(values.mean()),'incident_bootstrap_95ci':np.quantile(boot,[.025,.975]).tolist(),
                        'condition_macro_AC@1_delta':float(condition_values.mean()),
                        'condition_bootstrap_95ci':np.quantile(cboot,[.025,.975]).tolist(),
                        'paired_sign_flip_p':p,'n_incidents':len(values),'n_conditions':len(condition_values)}
    primary=['patch->patch-minus-runtime->runtime','patch->patch-minus-node_only->node_only']
    ordered=sorted(primary,key=lambda k:contrasts[k]['paired_sign_flip_p'])
    for i,key in enumerate(ordered):contrasts[key]['holm_p_two_primary']=min(1.0,contrasts[key]['paired_sign_flip_p']*(len(ordered)-i))
    subgroup={}
    group_cases={'all':test}
    for case in test:
        row=manifest[case]
        group_cases.setdefault(row['app'],[]).append(case);group_cases.setdefault(f"{row['app']}:{row['scenario']}",[]).append(case)
        group_cases.setdefault('patch_vs_runtime_changed' if not audit[case]['patch_equals_runtime'] else 'patch_vs_runtime_unchanged',[]).append(case)
    for name,cases in group_cases.items():
        subgroup[name]={'n':len(cases)}
        for label in ('reference->reference','runtime->runtime','patch->patch','node_only->node_only'):
            subgroup[name][label+'_AC@1']=statistics.mean(per_incident[c][label] for c in cases)
    output={'status':'completed_m1_matched_training_analysis','test_incidents':len(test),'seeds':[42,43,44],
            'matrix':matrix,'primary_contrasts':contrasts,'subgroups':subgroup,
            'limits':['Same architecture/hyperparameters and fixed split; validation selection occurs within each training condition.',
                      'node_only retains patch nodes/features/candidates and removes message-passing edges.',
                      'Incident-weighted and condition-macro contrasts are distinct estimands; recurring templates limit unseen-condition significance claims.']}
    out=Path(args.out_dir)
    if out.exists():raise SystemExit('Refusing overwrite')
    out.mkdir(parents=True);(out/'analysis.json').write_text(json.dumps(output,indent=2)+'\n')
    lines=['# M1 matched-training graph matrix','', '| Train \\ Eval | Reference | Runtime | Patch | Node-only |','|---|---:|---:|---:|---:|']
    for train in ('reference','runtime','patch','node_only'):
        cells=[]
        for variant in ('reference','runtime','patch','node_only'):
            value=matrix[train].get(variant);cells.append('--' if value is None else f"{value['mean']['AC@1']:.4f} ± {value['sample_std']['AC@1']:.4f}")
        lines.append('| '+' | '.join([train,*cells])+' |')
    lines+=['','## Primary paired AC@1 contrasts','']
    for name,value in contrasts.items():lines.append(f"- {name}: incident-weighted {value['AC@1_delta']:+.4f}, incident CI {value['incident_bootstrap_95ci']}; condition-macro {value['condition_macro_AC@1_delta']:+.4f}, condition CI {value['condition_bootstrap_95ci']}; incident sign-flip p={value['paired_sign_flip_p']:.5f}"+(f", Holm={value['holm_p_two_primary']:.5f}" if 'holm_p_two_primary' in value else ''))
    lines+=['','## Application and changed-input strata','',
            '| Group | n | Ref | Runtime | Patch | Node-only |','|---|---:|---:|---:|---:|---:|']
    for name,value in subgroup.items():
        lines.append(f"| {name} | {value['n']} | {value['reference->reference_AC@1']:.4f} | {value['runtime->runtime_AC@1']:.4f} | {value['patch->patch_AC@1']:.4f} | {value['node_only->node_only_AC@1']:.4f} |")
    lines+=['','## Limits','',*['- '+x for x in output['limits']]];(out/'REPORT.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(contrasts,indent=2))


if __name__=='__main__':main()
