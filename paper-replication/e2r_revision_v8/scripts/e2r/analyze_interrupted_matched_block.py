#!/usr/bin/env python3
"""Analyze the preserved interrupted matched block without inventing a pair."""
from __future__ import annotations

import argparse,hashlib,json,math,statistics
from pathlib import Path


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def p95(values):
    ordered=sorted(values);return ordered[max(0,math.ceil(.95*len(ordered))-1)]


def verify_window(window,raw):
    latencies=raw['latencies_ms'];attempts=raw['attempts'];successes=raw['successes']
    if attempts!=len(latencies) or attempts!=window['samples']:raise ValueError('sample coverage mismatch')
    if abs(p95(latencies)-window['p95_ms'])>1e-9:raise ValueError('p95 mismatch')
    elapsed=raw['elapsed_seconds']
    if abs(successes/elapsed-window['throughput_rps'])>1e-9:raise ValueError('throughput mismatch')
    errors=(attempts-successes)/attempts
    if abs(errors-window['error_rate'])>1e-12:raise ValueError('error-rate mismatch')
    return {'window_id':window['window_id'],'samples':attempts,'successes':successes,
            'throughput_rps':window['throughput_rps'],'error_rate':errors,
            'p95_ms':window['p95_ms'],'state_canary_ok':window['state_canary_ok'],
            'start_time':window['start_time'],'end_time':window['end_time']}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run-dir',required=True)
    parser.add_argument('--out-dir',required=True);args=parser.parse_args()
    root=Path(args.run_dir);out=Path(args.out_dir)
    if out.exists():raise SystemExit('Refusing overwrite')
    paths={'block':root/'block.json','no_action':root/'episodes/00_no_action.json',
           'typed':root/'episodes/01_typed_cleanup.json','final':root/'block.interruption.json',
           'typed_sidecar':root/'episodes/01_typed_cleanup.interruption.json'}
    rows={key:json.loads(path.read_text()) for key,path in paths.items()}
    final=rows['final']
    actual={'block.json':sha(paths['block']),'00_no_action.json':sha(paths['no_action']),
            '01_typed_cleanup.json':sha(paths['typed'])}
    if actual!=final['source_hashes']:raise ValueError('source hash mismatch')
    if final['valid_matched_comparison'] is not False or final['complete_matched_pairs']!=0:
        raise ValueError('interruption validity contract mismatch')
    no_action=rows['no_action'];typed=rows['typed']
    no_windows=[verify_window(no_action['pre_window'],no_action['pre_raw']),
                verify_window(no_action['fault_window'],no_action['fault_raw'])]
    no_windows += [verify_window(window,raw) for window,raw in
                   zip(no_action['evaluation_windows'],no_action['evaluation_raw'])]
    typed_windows=[verify_window(typed['pre_window'],typed['pre_raw']),
                   verify_window(typed['fault_window'],typed['fault_raw'])]
    output={'status':'completed_interrupted_block_analysis','evaluation_type':'live_isolated_partial',
        'valid_matched_comparison':False,'complete_matched_pairs':0,
        'accounting':{key:final[key] for key in ('planned_episode_count','episode_attempts_started',
            'injection_attempts','confirmed_fault_injections','injection_unknown_outcomes',
            'repair_process_invocations','repair_process_unknown_outcomes','confirmed_repair_mutations',
            'oracle_statuses')},
        'no_action':{'status':no_action['status'],'windows':no_windows,
            'technical_checks':no_action['technical']['checks'],'oracle_status':no_action['oracle']['status'],
            'final_observation_fault_age_seconds':no_action['final_observation_fault_age_seconds'],
            'cleanup_completion_fault_age_seconds':no_action['cleanup_completion_fault_age_seconds'],
            'cleanup_verified_absent':no_action['cleanup_verified_absent']},
        'typed_partial':{'status':rows['typed_sidecar']['status'],'windows':typed_windows,
            'repair_executed':typed['repair_executed'],'execution':typed['execution'],
            'saved_evaluation_window_count':rows['typed_sidecar']['saved_evaluation_window_count'],
            'in_flight_evaluation_requests_unknown':rows['typed_sidecar']['in_flight_evaluation_requests_unknown'],
            'oracle_status':None,'late_fault_absence':rows['typed_sidecar']['late_fault_absence'],
            'late_runtime_matches_block_start':rows['typed_sidecar']['late_runtime_matches_block_start']},
        'source_sha256':{key:sha(path) for key,path in paths.items()},
        'limits':['The typed episode was interrupted before a complete evaluation window was saved.',
                  'No typed/no-action business delta, effect size, recovery rate or causal comparison is defined.',
                  'Late absence checks cannot replace contemporaneous typed technical/oracle/cleanup evidence.',
                  'The complete no-action trajectory remains a valid standalone persistent-fault observation.']}
    out.mkdir(parents=True);(out/'analysis.json').write_text(json.dumps(output,indent=2)+'\n')
    lines=['# Interrupted matched recovery block','',
        '**Comparison status: invalid (0 complete matched pairs).**','','## Preserved accounting','',
        f"- Planned / attempted / confirmed injections: {final['planned_episode_count']} / {final['episode_attempts_started']} / {final['confirmed_fault_injections']}",
        f"- Repair process / confirmed mutation: {final['repair_process_invocations']} / {final['confirmed_repair_mutations']}",
        f"- Oracle statuses: {final['oracle_statuses']}",'','## No-action complete trajectory','',
        '| Window | p95 ms | rps | error |','|---|---:|---:|---:|']
    for row in no_windows:lines.append(f"| {row['window_id']} | {row['p95_ms']:.3f} | {row['throughput_rps']:.4f} | {row['error_rate']:.3f} |")
    lines += ['','No-action retained the exact injected UID through all recorded liveness probes; joint oracle FAIL and final cleanup NotFound were recorded.','',
              '## Typed partial trajectory','',
              f"Pre/fault p95: {typed_windows[0]['p95_ms']:.3f} / {typed_windows[1]['p95_ms']:.3f} ms.",
              f"Scoped deletion returned {typed['execution']['returncode']} at fault age {typed['execution']['repair_completion_fault_age_seconds']:.3f} s.",
              'The session was interrupted before any complete typed evaluation window, raw technical package, oracle, or contemporaneous cleanup record was saved.','',
              '## Limits','',*['- '+item for item in output['limits']]]
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'valid_matched_comparison':False,'complete_matched_pairs':0,
                      'no_action_oracle':'FAIL','typed_oracle':None},indent=2))


if __name__=='__main__':main()
