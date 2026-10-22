#!/usr/bin/env python3
"""Write non-mutating sidecars for the interrupted matched recovery block."""
from __future__ import annotations

import argparse,hashlib,json,sys,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.PlanRCAThinkRemed.cluster_client import ClusterClientContext,DEFAULT_CLUSTER_CONTEXT
from scripts.e2r.run_recovery_matched_block import get_pods,run,save
from scripts.e2r.run_recovery_sanity import EXPECTED_SCOPED_KUBECONFIG

EXPECTED_ROOT=(ROOT/'experiments/e2r/runs/recovery_matched_frontend_cpu_v1').resolve()
EXPECTED_SOURCE_HASHES={
    'block.json':'59f875fffb5ffdbc55f823e617ffa18963323d02aa4e0605f27dcf8b72c5d925',
    '00_no_action.json':'ab2ddd9dac089013b11b5e5b97e8a062bb6006985230b1469649113f7c714a0e',
    '01_typed_cleanup.json':'bb7f73d828bd44eb5fd90e68ded1796d7bffcb46c79c910473de779b0092f00d',
}


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',default=str(EXPECTED_ROOT));args=parser.parse_args()
    root=Path(args.run_dir).resolve()
    if root!=EXPECTED_ROOT:raise ValueError('finalizer is bound to the interrupted matched block')
    block_path=root/'block.json';no_action_path=root/'episodes/00_no_action.json'
    typed_path=root/'episodes/01_typed_cleanup.json'
    outputs=[root/'block.interruption.json',root/'episodes/01_typed_cleanup.interruption.json']
    if any(path.exists() for path in outputs):raise SystemExit('Refusing overwrite interruption sidecars')
    source_paths={'block.json':block_path,'00_no_action.json':no_action_path,
                  '01_typed_cleanup.json':typed_path}
    sources={name:digest(path) for name,path in source_paths.items()}
    if sources!=EXPECTED_SOURCE_HASHES:raise ValueError('interrupted source SHA-256 mismatch')
    block=json.loads(block_path.read_text());no_action=json.loads(no_action_path.read_text())
    typed=json.loads(typed_path.read_text())
    if (block.get('status')!='in_progress' or no_action.get('status')!='complete_expected'
            or typed.get('status')!='in_progress' or typed.get('repair_executed') is not True
            or typed.get('evaluation_windows')!=[] or typed.get('evaluation_raw')!=[]
            or typed.get('technical') is not None or typed.get('oracle') is not None
            or typed.get('cleanup') is not None or typed.get('completed_at') is not None):
        raise ValueError('interrupted source artifact contract mismatch')
    scoped=ClusterClientContext(str(EXPECTED_SCOPED_KUBECONFIG));admin=DEFAULT_CLUSTER_CONTEXT
    absence={}
    for name in ('e2r-matched-cpu-noaction','e2r-matched-cpu-typed'):
        row=run(['kubectl','get','stresschaos',name,'-n','e2r-supplement-20260925','-o','json'],
                context=admin,timeout=5)
        absence[name]={'probe':row,'absent':row['returncode']!=0 and
            ('NotFound' in row['stderr'] or 'not found' in row['stderr'].lower())}
    if not all(value['absent'] for value in absence.values()):
        raise RuntimeError('an interrupted fault object still exists; refusing observational finalization')
    try:
        pod_result,pods,runtime=get_pods(scoped);runtime_error=None
        runtime_same=(runtime==block.get('initial_runtime')) if runtime is not None else None
    except Exception as exc:
        pod_result=None;pods=None;runtime=None;runtime_same=None
        runtime_error={'type':type(exc).__name__,'error':str(exc)}
    captured_at=time.time()
    repair_wall=(typed.get('injection_request_started_at',captured_at)
                 + (typed.get('execution') or {}).get('repair_completion_fault_age_seconds',0))
    typed_sidecar={'schema':'e2r_interruption_sidecar_v1',
        'source_path':str(typed_path),'source_sha256':sources['01_typed_cleanup.json'],
        'status':'interrupted_after_typed_repair_before_recorded_evaluation_completion',
        'valid_recovery_outcome':False,'valid_matched_comparison_member':False,
        'saved_evaluation_window_count':0,'in_flight_evaluation_requests_unknown':True,
        'saved_oracle_status':None,
        'repair_process_attempted':typed.get('repair_process_attempted'),
        'repair_executed':typed.get('repair_executed'),
        'late_observation_captured_at':captured_at,
        'repair_completion_wall_time_reconstructed_from_trace':repair_wall,
        'late_observation_delay_from_repair_seconds':captured_at-repair_wall,
        'late_fault_absence':absence,'late_pod_probe':pod_result,'late_pods':pods,
        'late_runtime':runtime,'late_runtime_observation_error':runtime_error,
        'late_runtime_matches_block_start':runtime_same,
        'limits':['Late observations are not contemporaneous cleanup or recovery evidence.',
                  'Original partial episode JSON is unchanged and remains authoritative.',
                  'The episode cannot be rerun/replaced under the frozen block.']}
    block_sidecar={'schema':'e2r_interrupted_block_finalization_v1',
        'source_run':str(root),'source_hashes':sources,'status':'failed_interrupted',
        'planned_episode_count':2,'episode_attempts_started':2,'injection_attempts':2,
        'confirmed_fault_injections':2,'injection_unknown_outcomes':0,
        'repair_process_invocations':1,'repair_process_unknown_outcomes':0,
        'confirmed_repair_mutations':1,'oracle_statuses':['FAIL',None],
        'complete_matched_pairs':0,'no_action_complete':True,'typed_episode_interrupted':True,
        'typed_in_flight_evaluation_requests_unknown':True,
        'valid_matched_comparison':False,'rerun_or_replacement_authorized':False,
        'late_fault_absence':absence,'late_runtime_matches_block_start':runtime_same,
        'finalized_at':captured_at,
        'limits':['This sidecar terminalizes accounting without altering source artifacts.',
                  'Late state cannot fill missing typed evaluation/technical/oracle/cleanup fields.']}
    rechecked={name:digest(path) for name,path in source_paths.items()}
    if rechecked!=sources:raise RuntimeError('interrupted source changed before sidecar write')
    save(outputs[1],typed_sidecar);save(outputs[0],block_sidecar)
    print(json.dumps({'status':block_sidecar['status'],'complete_matched_pairs':0,
                      'late_faults_absent':True,'runtime_matches':runtime_same},indent=2))


if __name__=='__main__':main()
