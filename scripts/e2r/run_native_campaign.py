#!/usr/bin/env python3
"""Run the reviewed, isolated native intervention campaign and persist all attempts."""
import argparse
import json
import fcntl
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.campaign import run_campaign
from methods.E2R.intervention.native import NativeBackend

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,required=True);parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    config=json.loads(args.config.read_text())
    if config.get('resource_lock_directory'):
        from methods.E2R.intervention.platform import identifier
        locks=Path(config['resource_lock_directory']);locks.mkdir(parents=True,exist_ok=True)
        lock=(locks/(identifier(config['experimental_namespace'])+'.lock')).open('a')
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    backend=NativeBackend
    if config.get('mode')=='live_policy_comparison':
        from methods.E2R.intervention.policy_campaign import NativePolicyBackend
        backend=NativePolicyBackend
    elif config.get('mode')=='fixed_observation_evidence':
        if config.get('state_continuity'):
            from methods.E2R.intervention.stateful_evidence import StatefulEvidenceBackend
            backend=StatefulEvidenceBackend
        else:
            from methods.E2R.intervention.evidence_campaign import NativeFixedEvidenceBackend
            backend=NativeFixedEvidenceBackend
    elif config.get('mode')=='goal_policy_comparison':
        from methods.E2R.intervention.goal_policy import NativeGoalPolicyBackend
        backend=NativeGoalPolicyBackend
    elif config.get('mode')=='laboratory_policy_comparison':
        from methods.E2R.intervention.laboratory_campaign import NativeLaboratoryPolicyBackend
        backend=NativeLaboratoryPolicyBackend
    if config.get('action_context_schema'):
        if config['action_context_schema']!='action_context_v1':raise ValueError('unsupported action context schema')
        from methods.E2R.intervention.context_policy import ContextEvidenceBackend,NativeContextPolicyBackend
        if config.get('mode')=='fixed_observation_evidence':backend=ContextEvidenceBackend
        elif config.get('mode')=='goal_policy_comparison':backend=NativeContextPolicyBackend
        else:raise ValueError('action context requires registered fixed or goal policy mode')
    result=run_campaign(config,args.out,backend)
    print(json.dumps(result),flush=True)
    raise SystemExit(0 if result["status"]=="complete" else 2)
