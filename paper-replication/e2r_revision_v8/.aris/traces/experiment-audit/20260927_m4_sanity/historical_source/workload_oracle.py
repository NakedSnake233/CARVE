"""Independent active-workload collector for the bounded recovery experiment."""

from __future__ import annotations

import concurrent.futures
import json
import math
import select
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from methods.PlanRCAThinkRemed.cluster_client import (
    ClusterClientContext,
    DEFAULT_CLUSTER_CONTEXT,
)
from methods.PlanRCAThinkRemed.schemas import RepairPlan
from methods.PlanRCAThinkRemed.safe_probe_agent import run_probes

from .recovery_oracle import WorkloadWindow, evaluate_recovery


class KubectlPortForward:
    """Port-forward one service using the same client context as repair gates."""

    def __init__(self, *, namespace: str, service: str, local_port: int,
                 remote_port: int = 80,
                 client_context: ClusterClientContext = DEFAULT_CLUSTER_CONTEXT,
                 startup_timeout: float = 15.0):
        self.namespace = namespace
        self.service = service
        self.local_port = int(local_port)
        self.remote_port = int(remote_port)
        self.client_context = client_context
        self.startup_timeout = float(startup_timeout)
        self.process: Optional[subprocess.Popen] = None
        self.startup_log: List[str] = []

    def __enter__(self):
        # Refuse an occupied port before launching.  Socket-connect alone cannot
        # prove that the listener belongs to this kubectl process.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            try:
                reservation.bind(('127.0.0.1', self.local_port))
            except OSError as exc:
                raise RuntimeError(
                    f'local port {self.local_port} is already in use'
                ) from exc
        argv = [
            'kubectl', 'port-forward', '-n', self.namespace,
            f'service/{self.service}', f'{self.local_port}:{self.remote_port}',
            '--address=127.0.0.1',
        ]
        self.process = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=self.client_context.environment(), start_new_session=True,
        )
        deadline = time.monotonic() + self.startup_timeout
        forwarding_ready = False
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                _, stderr = self.process.communicate(timeout=1)
                raise RuntimeError(f'kubectl port-forward exited early: {stderr[:300]}')
            streams = [stream for stream in (self.process.stdout, self.process.stderr)
                       if stream is not None]
            readable, _, _ = select.select(streams, [], [], .1)
            for stream in readable:
                line = stream.readline().strip()
                if line:
                    self.startup_log.append(line)
                    if (f'Forwarding from 127.0.0.1:{self.local_port} -> '
                            in line):
                        forwarding_ready = True
            if forwarding_ready:
                try:
                    with socket.create_connection(
                        ('127.0.0.1', self.local_port), timeout=.2
                    ):
                        return self
                except OSError:
                    pass
        self.__exit__(None, None, None)
        raise TimeoutError('kubectl port-forward did not become ready')

    def __exit__(self, _type, _value, _traceback):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.process = None


@dataclass
class HTTPWindowCollector:
    url: str
    source_id: str
    request_count: int = 30
    workers: int = 5
    timeout: float = 5.0
    window_duration_seconds: Optional[float] = None
    offered_rps: Optional[float] = None
    state_canary: Optional[Callable[[], bool | None]] = None
    request_fn: Optional[Callable[[str, float], Tuple[bool, float]]] = None

    def _request(self) -> Tuple[bool, float]:
        if self.request_fn is not None:
            return self.request_fn(self.url, self.timeout)
        started = time.perf_counter()
        try:
            request = urllib.request.Request(
                self.url, headers={'User-Agent': 'E2R-recovery-oracle/1.0'}
            )
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response.read(1024)
                ok = 200 <= int(response.status) < 400
        except (urllib.error.URLError, TimeoutError, OSError):
            ok = False
        return ok, (time.perf_counter() - started) * 1000.0

    def collect(self, window_id: str) -> Tuple[WorkloadWindow, Dict[str, Any]]:
        if self.request_count < 1 or self.workers < 1:
            raise ValueError('request_count and workers must be positive')
        duration_mode = self.window_duration_seconds is not None or self.offered_rps is not None
        if duration_mode and (not isinstance(self.window_duration_seconds,(int,float))
                              or not isinstance(self.offered_rps,(int,float))
                              or isinstance(self.window_duration_seconds,bool)
                              or isinstance(self.offered_rps,bool)
                              or not math.isfinite(self.window_duration_seconds)
                              or not math.isfinite(self.offered_rps)
                              or self.window_duration_seconds<=0 or self.offered_rps<=0):
            raise ValueError('duration mode requires positive finite duration and offered_rps')
        start_wall = time.time()
        start_mono = time.perf_counter()
        sample_count=(max(1,math.ceil(self.window_duration_seconds*self.offered_rps))
                      if duration_mode else self.request_count)
        def scheduled_request(index):
            if duration_mode:
                scheduled=start_mono+(index+1)/self.offered_rps
                delay=scheduled-time.perf_counter()
                if delay>0:time.sleep(delay)
            return self._request()
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(self.workers, sample_count)
        ) as pool:
            samples = list(pool.map(scheduled_request,range(sample_count)))
        end_wall = time.time()
        elapsed = max(time.perf_counter() - start_mono, 1e-9)
        latencies = sorted(float(latency) for _, latency in samples
                           if isinstance(latency, (int, float))
                           and not isinstance(latency, bool)
                           and math.isfinite(latency) and latency >= 0)
        successes = sum(1 for ok, _ in samples if ok is True)
        p95 = (latencies[max(0, math.ceil(.95 * len(latencies)) - 1)]
               if len(latencies) == len(samples) and latencies else float('nan'))
        canary = self.state_canary() if self.state_canary is not None else None
        window = WorkloadWindow(
            samples=len(samples),
            throughput_rps=successes / elapsed,
            error_rate=(len(samples) - successes) / len(samples),
            p95_ms=p95,
            state_canary_ok=canary,
            window_id=window_id,
            source_id=self.source_id,
            start_time=start_wall,
            end_time=end_wall,
        )
        return window, {
            'window_id': window_id,
            'attempts': len(samples),
            'successes': successes,
            'elapsed_seconds': elapsed,
            'latencies_ms': latencies,
            'valid_latency_samples': len(latencies),
            'offered_rps': self.offered_rps,
            'target_window_seconds': self.window_duration_seconds,
        }


def _json_probe(command: str, *, client_context: ClusterClientContext,
                expected_kind: str, expected_name: str,
                expected_namespace: str) -> Tuple[str, Any]:
    rows = run_probes([command], timeout=15, client_context=client_context)
    if len(rows) != 1:
        return 'UNKNOWN', None
    row = rows[0]
    if (row.get('command') != command
            or row.get('client_context') != client_context.to_dict()
            or row.get('allowed') is not True
            or row.get('timed_out') is not False
            or type(row.get('returncode')) is not int):
        return 'UNKNOWN', None
    stderr = str(row.get('stderr') or '')
    if row.get('returncode') != 0:
        if 'NotFound' in stderr or 'not found' in stderr.lower():
            return 'NOT_FOUND', None
        return 'UNKNOWN', None
    try:
        value = json.loads(str(row.get('stdout') or ''))
    except (TypeError, ValueError):
        return 'UNKNOWN', None
    if not isinstance(value, dict) or value.get('kind') != expected_kind:
        return 'UNKNOWN', None
    metadata = value.get('metadata') or {}
    if (metadata.get('name') != expected_name
            or metadata.get('namespace') != expected_namespace):
        return 'UNKNOWN', None
    return 'PASS', value


def collect_technical_checks(
    plan: RepairPlan,
    *,
    client_context: ClusterClientContext = DEFAULT_CLUSTER_CONTEXT,
) -> Dict[str, bool | None]:
    """Measure the three technical predicates without using hidden labels."""
    actions = [action for action in plan.actions
               if action.action_type != 'WaitAndVerify']
    if len(actions) != 1:
        return {'fault_absent': None, 'resource_restored': None,
                'ready_endpoints': None}
    action = actions[0]
    service = plan.rca_report.root_cause_service
    namespace = action.namespace
    if not service or not namespace:
        return {'fault_absent': None, 'resource_restored': None,
                'ready_endpoints': None}

    if action.action_type in {'RemoveInjectedStress', 'RecoverNetworkFault',
                              'RecoverPodFailure'}:
        resource = {
            'StressChaos': 'stresschaos', 'NetworkChaos': 'networkchaos',
            'PodChaos': 'podchaos', 'IOChaos': 'iochaos',
        }.get(action.target_kind)
        status, _ = _json_probe(
            f'kubectl get {resource} {action.target_name} -n {namespace} -o json',
            client_context=client_context,
            expected_kind=action.target_kind,expected_name=action.target_name,
            expected_namespace=namespace,
        ) if resource else ('UNKNOWN', None)
        fault_absent = True if status == 'NOT_FOUND' else (
            False if status == 'PASS' else None)
    elif action.action_type == 'RecoverConfigError':
        status, deployment = _json_probe(
            f'kubectl get deployment {action.target_name} -n {namespace} -o json',
            client_context=client_context,
            expected_kind='Deployment',expected_name=action.target_name,
            expected_namespace=namespace,
        )
        fault_absent = None
        if status == 'PASS' and isinstance(deployment, dict):
            containers = (((deployment.get('spec') or {}).get('template') or {})
                          .get('spec') or {}).get('containers') or []
            row = next((item for item in containers if isinstance(item, dict)
                        and item.get('name') == action.target_container), None)
            resources = (row or {}).get('resources') or {}
            fault_absent = resources == {
                'limits': {'cpu': '500m', 'memory': '512Mi'},
                'requests': {'cpu': '100m', 'memory': '128Mi'},
            }
    else:
        fault_absent = None

    dep_status, deployment = _json_probe(
        f'kubectl get deployment {service} -n {namespace} -o json',
        client_context=client_context,
        expected_kind='Deployment',expected_name=service,
        expected_namespace=namespace,
    )
    resource_restored = None
    if dep_status == 'PASS' and isinstance(deployment, dict):
        spec_replicas = (deployment.get('spec') or {}).get('replicas', 1)
        ready = (deployment.get('status') or {}).get('readyReplicas', 0)
        resource_restored = (isinstance(spec_replicas, int)
                             and isinstance(ready, int)
                             and ready >= spec_replicas > 0)

    ep_status, endpoints = _json_probe(
        f'kubectl get endpoints {service} -n {namespace} -o json',
        client_context=client_context,
        expected_kind='Endpoints',expected_name=service,
        expected_namespace=namespace,
    )
    ready_endpoints = None
    if ep_status == 'PASS' and isinstance(endpoints, dict):
        addresses = [address for subset in endpoints.get('subsets') or []
                     for address in (subset.get('addresses') or [])]
        ready_endpoints = bool(addresses)
    return {'fault_absent': fault_absent,
            'resource_restored': resource_restored,
            'ready_endpoints': ready_endpoints}


def build_business_oracle(
    *,
    collector: HTTPWindowCollector,
    baseline: WorkloadWindow,
    plan: RepairPlan,
    required_windows: int = 3,
    inter_window_seconds: float = 1.0,
    require_state_canary: bool = False,
) -> Callable[..., Dict[str, Any]]:
    """Create the callback consumed by the coordinator after successful execution."""
    def callback(*, repair_completed_at: float,
                 client_context: ClusterClientContext) -> Dict[str, Any]:
        windows: List[WorkloadWindow] = []
        raw: List[Dict[str, Any]] = []
        for index in range(required_windows):
            window, diagnostics = collector.collect(f'post-{index}')
            windows.append(window); raw.append(diagnostics)
            if index + 1 < required_windows and inter_window_seconds > 0:
                time.sleep(inter_window_seconds)
        technical = collect_technical_checks(
            plan, client_context=client_context
        )
        result = evaluate_recovery(
            baseline=baseline,
            post_windows=windows,
            technical=technical,
            required_windows=required_windows,
            require_state_canary=require_state_canary,
            repair_completed_at=repair_completed_at,
            evaluated_at=time.time(),
        )
        result['raw_window_diagnostics'] = raw
        result['client_context'] = client_context.to_dict()
        return result
    return callback
