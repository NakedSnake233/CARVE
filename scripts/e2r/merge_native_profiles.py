#!/usr/bin/env python3
"""Merge exploratory profile receipts into a new, append-only exploration ledger."""
from __future__ import annotations
import argparse,json,shutil
from pathlib import Path

def merge(base, supplemental, out):
    base,supp,out=Path(base),Path(supplemental),Path(out)
    if out.exists(): raise FileExistsError(out)
    out.mkdir(parents=True)
    rows=[];base_rows=[]
    for source in (base,supp):
        path=source/'profiles.jsonl'
        if not path.exists(): raise FileNotFoundError(path)
        loaded=[json.loads(s) for s in path.read_text().splitlines() if s.strip()]
        if source==base: base_rows=loaded
        rows += loaded
    ids=[r['point']['id'] for r in rows]
    if len(ids)!=len(set(ids)): raise ValueError('duplicate exploratory profile point')
    with (out/'profiles.jsonl').open('x') as stream:
        for row in rows: stream.write(json.dumps(row,allow_nan=False)+'\n')
    for name in ('base_templates.json','profile_protocol.json'):
        shutil.copy2(base/name,out/name)
    protocol=json.loads((out/'profile_protocol.json').read_text())
    protocol['points'] += [r['point'] for r in rows[len(base_rows):]]
    protocol['merged_from']=[str(base.resolve()),str(supp.resolve())]
    (out/'profile_protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    summary={'status':'complete','planned':len(rows),'attempted':len(rows),'measured':sum(r['status']=='measured' for r in rows),
             'invalid':sum(r['status']!='measured' or r.get('restore',{}).get('status')!='restored' for r in rows),
             'purpose':'exploration only; append-only merge of real receipts; not formal episodes',
             'sources':[str(base.resolve()),str(supp.resolve())]}
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--base',type=Path,required=True);p.add_argument('--supplemental',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();print(json.dumps(merge(a.base,a.supplemental,a.out)))
