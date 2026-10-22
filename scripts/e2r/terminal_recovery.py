"""Post-campaign health evidence; never rewrite historical outcomes or summaries."""
import json
import copy
from pathlib import Path


def baseline_fields(directory,config):
    first=json.loads((Path(directory)/'order.json').read_text())[0]
    case=next(c for c in config['cases'] if (c['group_id'],c['condition_id'])==(first['group_id'],first['condition_id']))
    wanted={(c['entity'],c['path']) for c in case['baseline_changes']}
    fields={}
    for line in (Path(directory)/'episode-000000/setup_private.jsonl').read_text().splitlines():
        row=json.loads(line)
        if row['event']=='prepared':
            m=row['mutation'];entry={k:m[k] for k in ('entity','kind','name','uid','path')};entry['expected']=m['before']
        elif row['event']=='skipped_noop':
            entry={k:row[k] for k in ('entity','kind','name','uid','path')};entry['expected']=row['value']
        else:continue
        key=(entry['entity'],entry['path'])
        if key in wanted:fields.setdefault(key,entry)
    if set(fields)!=wanted:raise ValueError('incomplete original baseline field witness')
    return list(fields.values())


def canary_specs(config):
    specs=copy.deepcopy({**{k:v['canary'] for k,v in config['workloads'].items()},
            **{'direct:'+k:v['canary'] for k,v in config['direct_workloads'].items()}})
    # Match StatefulEvidenceBackend.finalize_campaign: acknowledged test carts
    # have been deliberately reset. This verifies endpoint health only, never
    # changes a recorded per-attempt continuity outcome.
    state=config.get('state_continuity')
    if state and specs.get('business',{}).get('continuity_check')==state.get('required_cart'):
        specs['business'].pop('continuity_check',None)
    if state and state.get('protocol')=='station_owned_fixture_native_readback_v1':
        from methods.E2R.intervention.station_goal import goal_step
        expected=goal_step(state['required_station'])
        business=specs.get('business',{})
        if [s for s in business.get('steps',[]) if s.get('station_goal_check')]!=[expected]:raise ValueError('registered Station terminal goal mismatch')
        business['steps']=[s for s in business['steps'] if not s.get('station_goal_check')]
    return specs


def validate_terminal_recovery(directory,config,summary,receipt):
    directory=Path(directory).resolve()
    prior=summary['campaign_baseline_restore']
    rows=[json.loads(l) for l in (directory/'episodes.jsonl').read_text().splitlines()]
    cleanup=receipt.get('scope_kind')=='post_outcome_cleanup'
    if cleanup:
        failed=[r for r in rows if r.get('restore',{}).get('status')=='failed']
        if (summary['status']!='stopped' or prior['status']!='restored'
                or not prior.get('native_canaries') or not all(prior['native_canaries'].values())
                or not config.get('state_continuity') or not failed
                or len(rows)!=summary['attempted'] or len(rows)>summary['planned']
                or any(r['status']!='observed' or r.get('execution',{}).get('status') not in ('executed','no_action') for r in rows)
                or receipt.get('recovered_episode_ids')!=[r['episode_id'] for r in failed]):
            raise ValueError('not a completed observed outcome with isolated cleanup failure')
        for r in failed:
            checks=r['restore'].get('checks',[])
            fields=[c for c in checks if 'restored' in c];controllers=[c for c in checks if 'controller_restore' in c]
            resets=[c for c in checks if 'reset_business_gate' in c]
            if not fields or not all(c['restored'] is True for c in fields) or not controllers or not all(c['controller_restore'] is True for c in controllers) or not resets:
                raise ValueError('cleanup recovery cannot repair unknown fields/controllers')
            if any(c['measurements']['business']['window']['state_canary_ok'] is not False for c in resets):
                raise ValueError('cleanup failure is not the registered state-goal mismatch')
    elif (summary['status']!='stopped' or prior['status']!='failed' or not prior.get('checks')
            or not all(c.get('restored') is True for c in prior['checks'])
            or not prior.get('native_canaries') or all(prior['native_canaries'].values())):
        raise ValueError('recovery applies only to final canary failure after field restoration')
    if (receipt.get('schema')!='native_terminal_health_recovery_v1' or receipt.get('status')!='verified'
            or receipt.get('source_directory')!=str(directory) or receipt.get('source_summary')!=summary
            or receipt.get('namespace')!=config['experimental_namespace']
            or receipt.get('canary_specs')!=canary_specs(config)
            or receipt.get('namespace_lock_held') is not True or receipt.get('mutations_performed') is not False):
        raise ValueError('terminal recovery provenance mismatch')
    expected=baseline_fields(directory,config)
    actual=receipt.get('baseline_checks',[])
    if len(actual)!=len(expected):raise ValueError('incomplete recovery baseline checks')
    for a,b in zip(actual,expected):
        if (any(a.get(k)!=v for k,v in b.items()) or a.get('actual')!=b['expected']
                or a.get('observed_uid')!=b['uid'] or a.get('matched') is not True):
            raise ValueError('terminal recovery baseline differs')
    deployments=receipt.get('deployment_checks',[])
    required={e['name'] for e in config['inventory']['entities'] if e.get('kind')=='Deployment'}
    names=[d['name'] for d in deployments]
    if not required<=set(names) or len(names)!=len(set(names)) or any(not d.get('ready') for d in deployments):
        raise ValueError('terminal recovery controllers incomplete')
    rows=[json.loads(l) for l in (directory/'episodes.jsonl').read_text().splitlines()]
    if not cleanup and (len(rows)!=summary['planned'] or any(r.get('restore',{}).get('status')!='restored' for r in rows)):
        raise ValueError('terminal recovery does not repair incomplete episodes')
    rounds=receipt.get('canary_rounds',[])
    if len(rounds)!=3:raise ValueError('three complete recovery canary rounds required')
    previous=max(r['completed_at'] for r in rows)
    for round in rounds:
        if (round['started_at']<=previous or round['completed_at']<round['started_at']
                or set(round['checks'])!=set(canary_specs(config))
                or any(c.get('success') is not True for c in round['checks'].values())):
            raise ValueError('recovery canary history invalid')
        previous=round['completed_at']
    if receipt.get('verified_at',0)<previous:raise ValueError('recovery timestamp precedes canaries')
    return receipt
