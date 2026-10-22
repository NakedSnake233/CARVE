"""Read observation-only E2R inputs and keep evaluator labels separate.

No metadata target/scenario, ground_truth, verifier annotations, post snapshot or
old plan cache is consumed by build_observation. The directory name is not emitted.
"""
from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def timestamp(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or '').strip()
    if not text:
        raise ValueError('missing observation timestamp')
    parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()



def read_json(path: Path) -> dict:
    obj = json.loads(path.read_text())
    if not isinstance(obj, dict):
        raise ValueError(f'expected object: {path.name}')
    return obj


def clean_topology(raw: dict) -> dict:
    # Only operational observations; never copy annotation/env/target/fault fields.
    result = {'timestamp': raw.get('timestamp')}
    fields = {
        'pods': ('name', 'service', 'version', 'node', 'ip', 'status'),
        'deployments': ('name', 'replicas', 'readyReplicas', 'version'),
        'services': ('name', 'endpoint_ips'),
    }
    for kind, allowed in fields.items():
        result[kind] = sorted(
            [{k: obj[k] for k in allowed if k in obj} for obj in raw.get(kind, []) if isinstance(obj, dict)],
            key=lambda obj: str(obj.get('name', '')),
        )
    return result


def _finite(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def build_observation(directory: str | Path, case_id: str) -> dict:
    directory = Path(directory)
    pre = clean_topology(read_json(directory/'topology/pre.json'))
    during = clean_topology(read_json(directory/'topology/during.json'))
    baseline_end, cutoff = timestamp(pre['timestamp']), timestamp(during['timestamp'])
    if cutoff <= baseline_end:
        raise ValueError('during cutoff must follow pre snapshot')
    start = baseline_end - 60.0
    all_services = {str(s['name']) for t in (pre, during) for s in t['services'] if s.get('name')}
    all_services |= {str(d['name']) for t in (pre, during) for d in t['deployments'] if d.get('name')}
    # Versions remain graph entities; service-level prediction space is declared once.
    candidates = sorted(s for s in all_services if not (s.endswith('-v2') and s[:-3] in all_services))
    if not candidates:
        raise ValueError('no observed service candidates')
    def canonical(service):
        service = str(service or '')
        return service[:-3] if service.endswith('-v2') and service[:-3] in all_services else service
    pod_owner = {p['name']: canonical(p.get('service')) for t in (pre, during) for p in t['pods'] if p.get('name')}
    stats = defaultdict(lambda: defaultdict(list))
    edge_counts = {'pre': defaultdict(int), 'during': defaultdict(int)}
    counts = {'metric_accepted': 0, 'metric_future_or_outside': 0, 'metric_missing_time': 0,
              'trace_accepted': 0, 'trace_future_or_outside': 0, 'trace_missing_time': 0,
              'log_accepted': 0, 'log_future_or_outside': 0, 'log_missing_time': 0}
    observed_max = None
    for path in sorted((directory/'metrics').glob('*.csv')):
        with path.open(newline='') as fh:
            for row in csv.DictReader(fh):
                try: t = timestamp(row.get('timestamp'))
                except (ValueError, TypeError, OverflowError):
                    counts['metric_missing_time'] += 1; continue
                if not start <= t <= cutoff:
                    counts['metric_future_or_outside'] += 1; continue
                value = _finite(row.get('value'))
                if value is None: continue
                try: labels = json.loads(row.get('labels_json') or '{}')
                except json.JSONDecodeError: continue
                if not isinstance(labels, dict): continue
                metric = str(row.get('metric_name') or path.stem)
                if metric == 'replica_count':
                    service = labels.get('deployment') or labels.get('service')
                else:
                    service = labels.get('destination_workload') or labels.get('service') or labels.get('deployment')
                if not service and labels.get('pod'): service = pod_owner.get(labels['pod'])
                service = canonical(service)
                if service not in candidates: continue
                phase = 'pre' if t <= baseline_end else 'during'
                metric = str(row.get('metric_name') or path.stem)
                stats[service][phase+':'+metric].append(value)
                if metric == 'request_rate':
                    stats[service][phase+':request_total'].append(max(value, 0))
                    if str(labels.get('response_code', '')).startswith('5'):
                        stats[service][phase+':request_errors'].append(max(value, 0))
                    source = canonical(labels.get('source_workload'))
                    if source in candidates and source != service and value > 0:
                        edge_counts[phase][(source, service)] += 1
                counts['metric_accepted'] += 1
                observed_max = t if observed_max is None else max(observed_max, t)
    # Parse compact traces only, and exclude any span completing after diagnosis.
    trace_path = directory/'traces/trace_summary.csv'
    spans = {}
    if trace_path.exists():
        with trace_path.open(newline='') as fh:
            for row in csv.DictReader(fh):
                try:
                    t = int(row['start_time_unix_nano']) / 1e9
                    duration = max(int(row.get('duration_nano') or 0), 0)/1e9
                except (ValueError, KeyError, TypeError):
                    counts['trace_missing_time'] += 1; continue
                if not (start <= t and t + duration <= cutoff):
                    counts['trace_future_or_outside'] += 1; continue
                service = canonical(row.get('service'))
                if service not in candidates: continue
                phase = 'pre' if t+duration <= baseline_end else 'during'
                stats[service][phase+':trace_ms'].append(duration*1000)
                code = str(row.get('status_code') or '').upper()
                error = code in {'2', 'ERROR', 'STATUS_CODE_ERROR'} or code.startswith('5')
                stats[service][phase+':trace_error'].append(float(error))
                spans[(row.get('trace_id'), row.get('span_id'))] = (service, phase, row)
                counts['trace_accepted'] += 1
                observed_max = t+duration if observed_max is None else max(observed_max,t+duration)
        for (trace_id, span_id), (service, phase, row) in spans.items():
            parent = spans.get((trace_id, row.get('parent_span_id')))
            if parent and parent[0] != service:
                edge_counts[phase][(parent[0], service)] += 1
            # Dataset client span operations use observed service DNS names.
            operation = str(row.get('operation', ''))
            for destination in candidates:
                if destination != service and operation.startswith(destination+'.'):
                    edge_counts[phase][(service, destination)] += 1
                    break
    logs = directory/'logs/all.jsonl'
    if logs.exists():
        with logs.open() as fh:
            for line in fh:
                try:
                    row = json.loads(line);t=timestamp(row.get('ts') or row.get('timestamp'))
                except (ValueError, TypeError):
                    counts['log_missing_time'] += 1;continue
                if not start <= t <= cutoff:
                    counts['log_future_or_outside'] += 1;continue
                service=canonical(row.get('service'))
                if service not in candidates:continue
                phase='pre' if t<=baseline_end else 'during'
                stats[service][phase+':log_error'].append(float(str(row.get('level','')).upper() in {'ERROR','FATAL','CRITICAL'}))
                counts['log_accepted']+=1
                observed_max=t if observed_max is None else max(observed_max,t)
    summary={}
    for service in candidates:
        summary[service]={}
        for key, values in sorted(stats[service].items()):
            if values:
                ordered=sorted(values)
                summary[service][key]={'mean':sum(values)/len(values),'max':max(values),'n':len(values),
                                      'p95':ordered[int((len(ordered)-1)*.95)]}
    return {'case_id':case_id,'candidates':candidates,'pre':pre,'during':during,
            'observed_metrics':summary,
            'call_edges':{phase:[{'source':a,'target':b,'count':n} for (a,b),n in sorted(edges.items())]
                          for phase,edges in edge_counts.items()},
            'window':{'start':start,'baseline_end':baseline_end,'cutoff':cutoff,'max_used_timestamp':observed_max},
            'coverage':counts}


def discover(data_root: str | Path) -> list[dict]:
    records=[]
    for path in sorted(Path(data_root).iterdir()):
        if not (path/'metadata.json').is_file() or not (path/'ground_truth.json').is_file():continue
        metadata=read_json(path/'metadata.json');truth=read_json(path/'ground_truth.json')
        records.append({'case_id':f'case{len(records)+1:05d}','directory':str(path),
                        'original_id':path.name,'app':metadata.get('environment',{}).get('namespace','unknown'),
                        'scenario':metadata.get('scenario_id','unknown'),
                        'true_service':truth.get('root_cause_service'),
                        'true_category':truth.get('fault_type'),
                        'fault_end_time':truth.get('fault_end_time')})
    return records
