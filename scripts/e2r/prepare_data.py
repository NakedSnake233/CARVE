#!/usr/bin/env python3
"""Prepare fresh cutoff-bounded E2R observation snapshots from real incidents."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import argparse,json,random,time
from collections import defaultdict,Counter
from methods.E2R.data import discover,build_observation,timestamp


def balanced(records,limit,seed):
    if not limit:return records
    groups=defaultdict(list)
    for row in records:groups[(row['app'],row['scenario'])].append(row)
    rng=random.Random(seed)
    for group in groups.values():rng.shuffle(group)
    chosen=[]
    while len(chosen)<limit:
        progress=False
        for key in sorted(groups):
            if groups[key] and len(chosen)<limit:
                chosen.append(groups[key].pop());progress=True
        if not progress:break
    return chosen


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data-root',required=True);ap.add_argument('--out-dir',required=True)
    ap.add_argument('--limit',type=int,default=40);ap.add_argument('--seed',type=int,default=42)
    ap.add_argument('--planner',default='observation-only',choices=['observation-only'])
    args=ap.parse_args();out=Path(args.out_dir)
    if (out/'manifest.json').exists():raise SystemExit('Refusing to overwrite an existing prepared campaign')
    (out/'observations').mkdir(parents=True,exist_ok=True)
    all_records=discover(args.data_root);records=balanced(all_records,args.limit,args.seed)
    manifest=[];skipped=[];start=time.perf_counter()
    for i,row in enumerate(records,1):
        t=time.perf_counter()
        try:
            obs=build_observation(row['directory'],row['case_id'])
            if row['true_service'] not in obs['candidates']:
                raise ValueError('ground truth absent from observed candidate universe; record excluded before inference')
            if row.get('fault_end_time') and obs['window']['cutoff']>=timestamp(row['fault_end_time']):
                raise ValueError('during snapshot is at/after recorded fault end')
            path=out/'observations'/f"{row['case_id']}.json"
            path.write_text(json.dumps(obs,sort_keys=True,separators=(',',':'))+'\n')
            manifest.append({**row,'observation_file':str(path.relative_to(out)),'preparation_seconds':time.perf_counter()-t,
                             'candidate_count':len(obs['candidates']),'coverage':obs['coverage']})
        except (OSError,ValueError,KeyError,TypeError) as exc:
            skipped.append({**row,'reason':str(exc),'error_type':type(exc).__name__})
        if i%10==0:print(f'prepared {i}/{len(records)} eligible={len(manifest)} excluded={len(skipped)}',flush=True)
    result={'data_root':str(Path(args.data_root).resolve()),'source_total':len(all_records),
            'requested':len(records),'eligible':len(manifest),'excluded':len(skipped),'seed':args.seed,
            'planner':args.planner,'seconds':time.perf_counter()-start,
            'schema':'e2r_observation_v2','input_policy':'pre/during observed facts and telemetry completed by during cutoff; no injected target/scenario/cache',
            'cases':manifest,'exclusions':skipped,
            'by_app':dict(Counter(r['app'] for r in manifest)),
            'topology_only_by_app':dict(Counter(r['app'] for r in manifest if not any(
                r['coverage'].get(k,0) for k in ['metric_accepted','trace_accepted','log_accepted'])))}
    (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ['cases','exclusions']},indent=2))

if __name__=='__main__':main()
