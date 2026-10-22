#!/usr/bin/env python3
"""Compare immutable isolated recovery calibration artifacts."""
from __future__ import annotations
import argparse,json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',nargs=2,required=True);parser.add_argument('--out-dir',required=True)
    args=parser.parse_args();rows=[]
    for path in args.baseline:
        value=json.loads(Path(path).read_text())
        if value.get('status')!='complete' or value.get('fault_injected') is not False:
            raise ValueError('baseline artifact is not a completed no-fault calibration')
        row={'path':str(Path(path).resolve()),'window_id':value['baseline']['window_id'],
             'samples':value['baseline']['samples'],'throughput_rps':value['baseline']['throughput_rps'],
             'error_rate':value['baseline']['error_rate'],'p95_ms':value['baseline']['p95_ms'],
             'state_canary_ok':value['baseline']['state_canary_ok'],
             'runtime_identity_stable':value.get('runtime_identity_stable_across_calibration')}
        rows.append(row)
    comparison={'throughput_ratio_v2_over_v1':rows[1]['throughput_rps']/rows[0]['throughput_rps'],
                'p95_ratio_v2_over_v1':rows[1]['p95_ms']/rows[0]['p95_ms'],
                'error_rate_delta':rows[1]['error_rate']-rows[0]['error_rate']}
    result={'status':'completed_no_fault_baseline_comparison','baselines':rows,
            'comparison':comparison,
            'limits':['The two calibrations occurred at different wall-clock times and are not randomized treatments.',
                      'V2 adds pre/post pod-identity and restart stability checks.']}
    out=Path(args.out_dir)
    if out.exists():raise SystemExit('Refusing overwrite')
    out.mkdir(parents=True);(out/'analysis.json').write_text(json.dumps(result,indent=2)+'\n')
    lines=['# Isolated no-fault calibration comparison','',
           '| Baseline | Samples | Throughput rps | Error rate | p95 ms | Canary | Runtime identity stable |',
           '|---|---:|---:|---:|---:|---:|---:|']
    for row in rows:lines.append(f"| {row['window_id']} | {row['samples']} | {row['throughput_rps']:.4f} | {row['error_rate']:.4f} | {row['p95_ms']:.3f} | {row['state_canary_ok']} | {row['runtime_identity_stable']} |")
    lines+=['',f"V2/V1 p95 ratio: {comparison['p95_ratio_v2_over_v1']:.4f}; throughput ratio: {comparison['throughput_ratio_v2_over_v1']:.4f}.",'',*['- '+item for item in result['limits']]]
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(comparison,indent=2))


if __name__=='__main__':main()
