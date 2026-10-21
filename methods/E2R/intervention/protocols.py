"""Observation protocol identities and outcome-blind replay bookkeeping."""
from __future__ import annotations
import hashlib
import json


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def protocol_descriptor(config, source):
    backgrounds = {}
    for name, value in config.get('background_workloads', {}).items():
        backgrounds[name] = {k:v for k,v in value.items() if k not in ('url', 'source_id')}
        if value.get('protocol') == 'redis_kv':
            backgrounds[name].setdefault('client_connection_budget', 96)
    descriptor = {'schema': 'native_observation_protocol_stratum_v1',
            'runtime': source['runtime_path'], 'source_snapshot_sha256': source['source_snapshot_sha256'],
            'dispatch_floor_seconds': config.get('dispatch_delay_floor_seconds', .25),
            'traffic_process': config.get('traffic_process', False),
            'continuous_workload': config.get('continuous_workload', False),
            'background_workloads': backgrounds, 'verification': config.get('verification', {}),
            'database_protocol': config.get('native_database_probe_protocol', {}).get('version'),
            'metrics_protocol': config.get('native_metrics_probe_protocol', {}).get('version'),
            'redis_observation_protocol': config.get('native_redis_observation_protocol', {}).get('version'),
            'probe_budgets': [{k:p.get(k) for k in ('id', 'kind', 'timeout', 'cost', 'ttl')}
                              for p in config.get('inventory', {}).get('probes', [])]}
    if config.get('observe_configuration_bindings',False):descriptor['observe_configuration_bindings']=True
    if config.get('native_roles'):descriptor['native_roles']=config['native_roles']
    if config.get('collector_query_order_protocol'):descriptor['collector_query_order_protocol']=config['collector_query_order_protocol']
    if config.get('synchronized_baseline_rollout_roles'):descriptor['synchronized_baseline_rollout_roles']=config['synchronized_baseline_rollout_roles']
    if config.get('state_continuity'):
        descriptor['state_continuity_protocol']=config['state_continuity']['protocol']
    if config.get('application_runtime_settings'):
        descriptor['application_runtime_settings']=config['application_runtime_settings']
    if config.get('baseline_latency_ceiling_ms') is not None:
        descriptor['baseline_latency_ceiling_ms']=config['baseline_latency_ceiling_ms']
    if config.get('goal_policy_protocol'):
        descriptor['goal_policy_protocol']=config['goal_policy_protocol']
    return descriptor


def stratum_id(descriptor):
    return 'protocol-' + hashlib.sha256(canonical(descriptor).encode()).hexdigest()[:20]


def protocol_key(row, match=True):
    return row.get('protocol_stratum', 'legacy_unspecified') if match else 'heterogeneous_protocol_pool'


def replay_key(row, match=True):
    return (row['application'] + '/' + row['group_id'], row['condition_id'], protocol_key(row, match))


def decision_key(row):
    return row['group'], row['condition'], row.get('protocol_stratum', 'legacy_unspecified')


def anchor_key(row):
    """Use original order then pre-action time, never outcome or packed filename order."""
    return (row.get('original_schedule_slot', float('inf')),
            row.get('input', {}).get('graph', {}).get('at', float('inf')), row['episode_id'])


def slot_key(row):
    slot = row.get('original_schedule_slot')
    return (row['application'], row['group_id'], slot if slot is not None else row['episode_id'])


def attempt_denominators(rows):
    unique = len({slot_key(r) for r in rows})
    return {'raw_attempts': len(rows), 'unique_slots': unique, 'retry_attempts': len(rows)-unique,
            'attempts_without_input': sum('input' not in r for r in rows)}
