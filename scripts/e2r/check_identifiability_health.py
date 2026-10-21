#!/usr/bin/env python3
"""One fixed healthy-load preflight; no injected fault or efficacy labels."""
import argparse
import copy
from dataclasses import asdict
import fcntl
import json
import math
from pathlib import Path
import sys
import time
import traceback
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from methods.E2R.intervention.campaign import KubernetesBackend, collector, http_request
from methods.E2R.intervention.native import NativeBackend


def healthy(window, spec, protocol):
    return (all(math.isfinite(v) for v in (window.error_rate, window.throughput_rps, window.p95_ms))
            and window.samples >= protocol.min_samples and window.state_canary_ok is True
            and window.error_rate <= .01 and window.throughput_rps >= .9 * spec['offered_rps']
            and window.p95_ms > 0)


def run(config, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    lock = (Path(config['resource_lock_directory']) / (config['experimental_namespace'] + '.lock')).open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    backend = NativeBackend(config, out)
    backend.current_dir = out
    case = next(c for c in config['cases'] if c['condition_id'] ==
                ('affinity_A' if config['experimental_namespace'].endswith('p3') else 'affinity_B'))
    specs = {role: {**copy.deepcopy(config['direct_workloads'][role]),
                   'offered_rps': 20. if role == 'peer' else 10., 'workers': 128}
             for role in config['native_roles']}
    protocol = {'purpose': 'healthy feasibility only; no background stress, no fault, no effect label',
                'config': config, 'baseline_case': case, 'streams': specs,
                'warmup_seconds': 10, 'windows': 3,
                'criteria': 'Every 10s window: >=20 samples, canary true, error<=.01, throughput>=.9 offered, finite positive p95. No tuned latency cutoff.'}
    (out / 'protocol.json').write_text(json.dumps(protocol, indent=2) + '\n')
    result = {'status': 'started', 'started_at': time.time(), 'purpose': protocol['purpose']}
    try:
        backend.reset_application_state(out, 'before')
        backend._setup(case['baseline_changes'])
        if not backend.wait_controllers():
            raise TimeoutError('healthy preflight controllers did not settle')
        backend.workloads = {name: collector(spec) for name, spec in specs.items()}
        backend.active_background_workloads = {}
        backend.start_traffic()
        cycles = backend.traffic.take(backend.traffic.origin_wall + 10, 3, 100)
        result['windows'] = [{name: {'window': asdict(w), 'raw': raw, 'healthy': healthy(w, specs[name], backend.protocol)}
                              for name, (w, raw) in cycle.items()} for cycle in cycles]
        result['healthy'] = all(r['healthy'] for cycle in result['windows'] for r in cycle.values())
    except Exception as exc:
        result.update(healthy=False, error=type(exc).__name__, detail=str(exc))
        (out / 'traceback.txt').write_text(traceback.format_exc())
    finally:
        # Drain first; never flush data while any traffic worker still owns it.
        try:
            KubernetesBackend.close_traffic(backend)
            result['traffic_drained'] = backend.traffic is None
        except Exception as exc:
            result['traffic_stop_error'] = str(exc)
        try:
            result['restore'] = backend.restore()
            if backend.traffic is None:
                backend.reset_application_state(out, 'after')
                result['restore_canaries'] = {name: http_request(spec['canary'])[0]
                                              for name, spec in config['direct_workloads'].items()}
        except Exception as exc:
            result['restore'] = {'status': 'failed', 'error': str(exc)}
        result['status'] = 'passed' if (result.get('healthy') and result.get('traffic_drained')
                and result.get('restore', {}).get('status') == 'restored'
                and all(result.get('restore_canaries', {}).values()) and result.get('restore_canaries')) else 'failed'
        result['completed_at'] = time.time()
        (out / 'result.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
        lock.close()
    print(json.dumps({k: result[k] for k in ('status', 'healthy', 'restore')}), flush=True)
    return 0 if result['status'] == 'passed' else 2


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    raise SystemExit(run(json.loads(a.config.read_text()), a.out))
