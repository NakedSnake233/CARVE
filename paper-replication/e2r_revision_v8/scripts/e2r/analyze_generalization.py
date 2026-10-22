#!/usr/bin/env python3
"""Summarize LOFO and cross-application patch-trained E2R runs."""
from pathlib import Path
import argparse,json,statistics


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--runs-root',default='experiments/e2r/runs');ap.add_argument('--out-dir',required=True);args=ap.parse_args()
    root=Path(args.runs_root);names=[f'lofo_{x}' for x in ('T1','T2','T3','T4')]+['boutique_to_trainticket','trainticket_to_boutique'];summary={}
    for name in names:
        directory=root/f'generalization_{name}_v1';result=json.loads((directory/'results.json').read_text())
        split=json.loads((directory/'split.json').read_text());rows=[json.loads(x) for x in (directory/'predictions.jsonl').read_text().splitlines()]
        if result['training_variant']!='patch' or result['seeds']!=[42,43,44]:raise ValueError(f'identity mismatch {name}')
        by_seed={}
        for seed in (42,43,44):
            group=[r for r in rows if r['seed']==seed and r['variant']=='patch'];n=len(group)
            if n!=len(split['test']):raise ValueError(f'coverage mismatch {name} seed{seed}')
            by_seed[str(seed)]={'AC@1':sum(r['rank']==1 for r in group)/n,'AC@3':sum(r['rank']<=3 for r in group)/n,
                                'AC@5':sum(r['rank']<=5 for r in group)/n,'MRR':sum(1/r['rank'] for r in group)/n}
        summary[name]={'split_counts':{k:len(v) for k,v in split.items()},'by_seed':by_seed,
                       'mean':{m:statistics.mean(row[m] for row in by_seed.values()) for m in ('AC@1','AC@3','AC@5','MRR')},
                       'sample_std':{m:statistics.stdev(row[m] for row in by_seed.values()) for m in ('AC@1','AC@3','AC@5','MRR')},
                       'training_seconds':sum(x['seconds'] for x in result['training'])}
    output={'status':'completed_generalization_analysis','evaluation_type':'real_gt','runs':summary,
            'limits':['LOFO tests only the held-out scenario portion of the original fixed test split, not all cases in that scenario.',
                      'All test condition groups are controlled templates; this is not open-world or production evidence.',
                      'Cross-application candidate/service identities are disjoint, while feature schema/model architecture are shared.']}
    out=Path(args.out_dir)
    if out.exists():raise SystemExit('Refusing overwrite')
    out.mkdir(parents=True);(out/'analysis.json').write_text(json.dumps(output,indent=2)+'\n')
    lines=['# E2R scenario/application generalization','', '| Run | train/val/test | AC@1 | AC@3 | AC@5 | MRR |','|---|---|---:|---:|---:|---:|']
    for name,value in summary.items():
        counts=value['split_counts'];line=[name,f"{counts['train']}/{counts['val']}/{counts['test']}"]
        line += [f"{value['mean'][m]:.4f} ± {value['sample_std'][m]:.4f}" for m in ('AC@1','AC@3','AC@5','MRR')]
        lines.append('| '+' | '.join(line)+' |')
    lines+=['','## Limits','',*['- '+x for x in output['limits']]];(out/'REPORT.md').write_text('\n'.join(lines)+'\n');print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
