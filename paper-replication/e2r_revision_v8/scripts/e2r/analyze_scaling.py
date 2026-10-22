#!/usr/bin/env python3
"""Compare CPU/GPU synthetic E2R scaling runs."""
from __future__ import annotations

import argparse,json,statistics
from collections import defaultdict
from pathlib import Path


def percentile(values,q):
    values=sorted(values);return values[min(len(values)-1,int((len(values)-1)*q))]


def load_run(path):
    root=Path(path);result=json.loads((root/'results.json').read_text())
    rows=[json.loads(line) for line in (root/'raw.jsonl').read_text().splitlines() if line]
    grouped=defaultdict(list)
    for row in rows:grouped[(row['candidate_services'],row['telemetry_factor'])].append(row)
    return result,grouped


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpu',required=True);parser.add_argument('--gpu',required=True)
    parser.add_argument('--out-dir',required=True);args=parser.parse_args()
    cpu,cgroups=load_run(args.cpu);gpu,ggroups=load_run(args.gpu)
    if set(cgroups)!=set(ggroups):raise ValueError('CPU/GPU condition mismatch')
    rows=[];parts=('catalog_seconds','serialization_seconds','execution_seconds','tensor_seconds','inference_seconds')
    for key in sorted(cgroups):
        item={'candidate_services':key[0],'telemetry_factor':key[1]}
        for label,groups in [('cpu',cgroups),('gpu',ggroups)]:
            values=[sum(row[part] for part in parts) for row in groups[key]]
            inference=[row['inference_seconds'] for row in groups[key]]
            item[label+'_pipeline_median_seconds']=statistics.median(values)
            item[label+'_pipeline_p95_seconds']=percentile(values,.95)
            item[label+'_inference_median_seconds']=statistics.median(inference)
            item[label+'_inference_p95_seconds']=percentile(inference,.95)
        item['inference_speedup_cpu_over_gpu']=(item['cpu_inference_median_seconds']/item['gpu_inference_median_seconds'])
        item['pipeline_speedup_cpu_over_gpu']=(item['cpu_pipeline_median_seconds']/item['gpu_pipeline_median_seconds'])
        rows.append(item)
    out=Path(args.out_dir)
    if out.exists():raise SystemExit('Refusing overwrite')
    out.mkdir(parents=True)
    result={'status':'completed_scaling_comparison','evaluation_type':'simulation_only',
            'cpu_device':cpu['device'],'gpu_device':gpu['device'],
            'gpu_peak_allocated_bytes':gpu['peak_allocated_bytes'],'conditions':rows,
            'limits':cpu['limits']}
    (out/'analysis.json').write_text(json.dumps(result,indent=2)+'\n')
    lines=['# E2R synthetic scaling comparison','',
           '| Candidates | Telemetry × | CPU inference ms | GPU inference ms | CPU/GPU inference | CPU pipeline ms | GPU pipeline ms |',
           '|---:|---:|---:|---:|---:|---:|---:|']
    for row in rows:
        lines.append('| {candidate_services} | {telemetry_factor} | {cpu_inference_median_seconds:.3f} | {gpu_inference_median_seconds:.3f} | {inference_speedup_cpu_over_gpu:.2f}× | {cpu_pipeline_median_seconds:.3f} | {gpu_pipeline_median_seconds:.3f} |'.format(
            **{**row,**{key:row[key]*1000 for key in row if key.endswith('_seconds')}}))
    lines += ['',f"GPU peak allocated memory: {gpu['peak_allocated_bytes']/1024**2:.2f} MiB.",'',
              'All fixtures replicate one recorded graph. These measurements characterize implementation overhead only; telemetry expansion adds serialized keys that the fixed feature extractor intentionally ignores.']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'conditions':len(rows),'gpu_peak_allocated_bytes':gpu['peak_allocated_bytes']},indent=2))


if __name__=='__main__':main()
