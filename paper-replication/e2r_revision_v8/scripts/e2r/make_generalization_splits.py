#!/usr/bin/env python3
"""Freeze leave-one-scenario-out and cross-application splits from the audited M1 split."""
from pathlib import Path
import argparse,json


def write(path,value):
    path.write_text(json.dumps(value,indent=2)+'\n')


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--manifest',required=True)
    ap.add_argument('--base-split',required=True);ap.add_argument('--out-dir',required=True);args=ap.parse_args()
    out=Path(args.out_dir)
    if out.exists():raise SystemExit('Refusing overwrite')
    out.mkdir(parents=True);manifest=json.loads(Path(args.manifest).read_text());by={r['case_id']:r for r in manifest['cases']}
    base=json.loads(Path(args.base_split).read_text())
    outputs={}
    for scenario in ('T1','T2','T3','T4'):
        value={'train':[c for c in base['train'] if by[c]['scenario']!=scenario],
               'val':[c for c in base['val'] if by[c]['scenario']!=scenario],
               'test':[c for c in base['test'] if by[c]['scenario']==scenario]}
        write(out/f'lofo_{scenario}.json',value);outputs[f'lofo_{scenario}']={k:len(v) for k,v in value.items()}
    for source,target in (('boutique','trainticket'),('trainticket','boutique')):
        value={'train':[c for c in base['train'] if by[c]['app']==source],
               'val':[c for c in base['val'] if by[c]['app']==source],
               'test':[c for c in base['test'] if by[c]['app']==target]}
        name=f'{source}_to_{target}';write(out/f'{name}.json',value);outputs[name]={k:len(v) for k,v in value.items()}
    write(out/'summary.json',outputs);print(json.dumps(outputs,indent=2))


if __name__=='__main__':main()
