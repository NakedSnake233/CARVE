"""Collect a unified :class:`MultimodalContext` from the live cluster (read-only).

This is the single, standardized place that turns a running incident into the
metrics / logs / traces / k8s-state / topology view every RCA algorithm sees.

Fairness knob ``hide_injection_artifacts`` (default True): the Chaos Mesh CRDs
are the *injection mechanism* and directly name the culprit, so by default they
are NOT exposed — RCA must diagnose from real telemetry (CPU/mem usage, pod
restarts/readiness, events, deployment specs, logs, traces). Everything else
(including deployment resource limits, which is how a config-error legitimately
manifests) is exposed.

Collection is read-only (kubectl get/top/logs) and degrades gracefully: any
probe failure yields a partial context rather than raising.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from .interface import MultimodalContext, ServiceMetrics, ServiceLogs, TraceEdge
from .topology import StaticTopologySource, TopologySource, derive_topology_from_traces

_SIDECAR_RE = re.compile(r"sidecar|busybox|istio-proxy", re.IGNORECASE)

# Telemetry infrastructure that may live in the SUT namespace but is never a
# fault target -- excluded from the RCA candidate set (see _services).
_INFRA_SERVICES = {"jaeger", "jaeger-collector", "jaeger-query"}


# ---------------------------------------------------------------------------
# small unit parsers
# ---------------------------------------------------------------------------

def _cpu_millicores(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    s = str(value).strip()
    try:
        if s.endswith("m"):
            return float(s[:-1])
        if s.endswith("n"):
            return float(s[:-1]) / 1e6
        if s.endswith("u"):
            return float(s[:-1]) / 1e3
        return float(s) * 1000.0
    except Exception:
        return None


def _mem_mib(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    s = str(value).strip()
    try:
        for suf, mul in (("Gi", 1024), ("Mi", 1), ("Ki", 1 / 1024),
                         ("G", 1000), ("M", 1), ("K", 1 / 1000)):
            if s.endswith(suf):
                return float(s[: -len(suf)]) * mul
        return float(s) / (1024 * 1024)   # bytes
    except Exception:
        return None


# ---------------------------------------------------------------------------
# pluggable trace source (default: none)
# ---------------------------------------------------------------------------

class TraceSource:
    def edges(self, namespace: str, env: str) -> List[TraceEdge]:
        return []


class NullTraceSource(TraceSource):
    """No tracing backend configured -> empty trace modality."""


# ---------------------------------------------------------------------------
# provider
# ---------------------------------------------------------------------------

class LiveClusterContextProvider:
    def __init__(self, *, hide_injection_artifacts: bool = True,
                 collect_logs: bool = True, log_tail: int = 40,
                 kubeconfig: Optional[str] = None,
                 trace_source: Optional[TraceSource] = None,
                 topology_source: Optional[TopologySource] = None,
                 known_services: Optional[List[str]] = None,
                 probe_network: bool = True, probe_disk: bool = True,
                 always_probe: bool = False, probe_timeout: int = 8,
                 probe_workers: int = 6, ping_target: str = "8.8.8.8",
                 disk_path: str = "/var/log/mysql"):
        self.hide_injection_artifacts = hide_injection_artifacts
        self.collect_logs = collect_logs
        self.log_tail = log_tail
        self.kubeconfig = kubeconfig or os.environ.get("KUBECONFIG") or "/etc/rancher/k3s/k3s.yaml"
        self.trace_source = trace_source or NullTraceSource()
        self.topology_source = topology_source or StaticTopologySource()
        self.known_services = known_services
        # active symptom probes (network latency/loss, disk write latency)
        self.probe_network = probe_network
        self.probe_disk = probe_disk
        self.always_probe = always_probe        # if False, probe only when no strong cpu/mem/config/pod anomaly
        self.probe_timeout = probe_timeout
        self.probe_workers = probe_workers
        self.ping_target = ping_target
        self.disk_path = disk_path

    # -- kubectl helpers -------------------------------------------------------
    def _run(self, args: List[str], timeout: int = 20) -> str:
        env = dict(os.environ)
        env["KUBECONFIG"] = self.kubeconfig
        try:
            r = subprocess.run(["kubectl", *args], capture_output=True, text=True,
                               timeout=timeout, env=env)
            return r.stdout
        except Exception:
            return ""

    def _json(self, args: List[str], timeout: int = 20) -> Any:
        out = self._run(args, timeout=timeout)
        try:
            return json.loads(out) if out.strip() else None
        except Exception:
            return None

    # -- collection ------------------------------------------------------------
    def collect(self, namespace: str, env: str) -> MultimodalContext:
        pods = self._json(["get", "pods", "-n", namespace, "-o", "json"]) or {"items": []}
        deploys = self._json(["get", "deploy", "-n", namespace, "-o", "json"]) or {"items": []}
        top = self._run(["top", "pods", "-n", namespace, "--containers", "--no-headers"])
        events = self._json(["get", "events", "-n", namespace, "-o", "json"]) or {"items": []}

        services = self._services(deploys, pods)
        metrics = self._metrics(pods, deploys, top, services)

        # Active symptom probes for network / disk faults (invisible in CPU/mem).
        # Run only when no strong resource/config/pod anomaly is present (i.e. the
        # fault is likely network or disk) unless always_probe. Symptom-based,
        # read-only-ish; NOT chaos-selector leakage.
        probed = []
        if (self.probe_network or self.probe_disk) and \
                (self.always_probe or not self._has_resource_anomaly(metrics)):
            pod_by_svc = self._pod_by_service(pods)
            if self.probe_network:
                self._network_probes(namespace, pod_by_svc, metrics)
                probed.append("network")
            if self.probe_disk:
                self._disk_probes(namespace, pod_by_svc, metrics)
                probed.append("disk")

        logs = self._logs(namespace, services) if self.collect_logs else {}
        traces = self.trace_source.edges(namespace, env)
        if traces:
            self._apply_trace_signals(traces, metrics)
            probed.append("traces")
        # Merge OBSERVED edges with the known static call graph. A fully-dead callee
        # (pod-fail) records no spans, so its inbound edge vanishes from the trace-
        # derived graph -- but causal/blame analysis still needs "who calls the dead
        # service", so we keep the static edges too (order: observed first).
        topology = list(dict.fromkeys(
            derive_topology_from_traces(traces) + self.topology_source.edges(namespace, env)))
        k8s_events = self._events(events)

        raw: Dict[str, Any] = {"deploy_json": deploys}
        modalities = ["metrics", "k8s_state", "topology"] + probed
        if logs:
            modalities.append("logs")
        if traces:
            modalities.append("traces")
        if not self.hide_injection_artifacts:
            # artifact-exposed mode: include chaos CRDs (trivializes RCA)
            raw["chaos_json"] = self._json(
                ["get", "stresschaos,networkchaos,podchaos,iochaos", "-n", namespace, "-o", "json"])

        summary = (f"Alert: degraded health detected for one or more services in "
                   f"namespace '{namespace}' (env '{env}'). Localize the root-cause "
                   f"service from the available telemetry.")

        return MultimodalContext(
            namespace=namespace, env=env, services=services, incident_summary=summary,
            metrics=metrics, logs=logs, traces=traces, topology=topology,
            k8s_events=k8s_events, raw=raw, available_modalities=modalities,
        )

    # -- per-modality builders -------------------------------------------------
    def _services(self, deploys: Dict[str, Any], pods: Dict[str, Any]) -> List[str]:
        if self.known_services:
            return list(self.known_services)
        names = set()
        for d in (deploys.get("items") or []):
            n = (d.get("metadata") or {}).get("name")
            if n:
                names.add(n)
        for p in (pods.get("items") or []):
            app = ((p.get("metadata") or {}).get("labels") or {}).get("app")
            if app:
                names.add(app)
        # Telemetry infra co-located in the SUT namespace (e.g. the Jaeger backend)
        # is never a fault target -- exclude it so it can't pollute RCA rankings.
        return sorted(n for n in names if n not in _INFRA_SERVICES)

    def _pod_app(self, pod: Dict[str, Any]) -> Optional[str]:
        labels = (pod.get("metadata") or {}).get("labels") or {}
        return labels.get("app") or labels.get("app.kubernetes.io/name")

    def _deploy_limits(self, deploys: Dict[str, Any]) -> Dict[str, Dict[str, Optional[float]]]:
        out: Dict[str, Dict[str, Optional[float]]] = {}
        for d in (deploys.get("items") or []):
            name = (d.get("metadata") or {}).get("name")
            try:
                containers = d["spec"]["template"]["spec"]["containers"]
            except Exception:
                continue
            for c in containers or []:
                if _SIDECAR_RE.search(str(c.get("name", ""))):
                    continue
                lim = (c.get("resources") or {}).get("limits") or {}
                out[name] = {"cpu": _cpu_millicores(lim.get("cpu")),
                             "mem": _mem_mib(lim.get("memory"))}
                break
        return out

    def _metrics(self, pods: Dict[str, Any], deploys: Dict[str, Any], top: str,
                 services: List[str]) -> Dict[str, ServiceMetrics]:
        limits = self._deploy_limits(deploys)
        m: Dict[str, ServiceMetrics] = {s: ServiceMetrics(service=s) for s in services}

        # pod -> app, plus readiness/restarts
        pod_app: Dict[str, str] = {}
        for p in (pods.get("items") or []):
            name = (p.get("metadata") or {}).get("name", "")
            app = self._pod_app(p)
            if not app or app in _INFRA_SERVICES:
                continue
            pod_app[name] = app
            sm = m.setdefault(app, ServiceMetrics(service=app))
            sm.total_pods += 1
            phase = (p.get("status") or {}).get("phase")
            ready = False
            for cond in (p.get("status") or {}).get("conditions") or []:
                if cond.get("type") == "Ready" and cond.get("status") == "True":
                    ready = True
            if ready and phase == "Running":
                sm.ready_pods += 1
            else:
                sm.not_ready += 1
            for cs in (p.get("status") or {}).get("containerStatuses") or []:
                sm.restarts += int(cs.get("restartCount") or 0)

        # usage from kubectl top (POD CONTAINER CPU MEM)
        usage: Dict[str, Dict[str, float]] = {}
        for line in (top or "").splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            pod_name, cname, cpu_s, mem_s = parts[0], parts[1], parts[2], parts[3]
            if _SIDECAR_RE.search(cname):
                continue
            app = pod_app.get(pod_name)
            if not app:
                continue
            cpu = _cpu_millicores(cpu_s) or 0.0
            mem = _mem_mib(mem_s) or 0.0
            u = usage.setdefault(app, {"cpu": 0.0, "mem": 0.0})
            u["cpu"] += cpu
            u["mem"] += mem

        for svc, sm in m.items():
            lim = limits.get(svc, {})
            sm.cpu_limit_millicores = lim.get("cpu")
            sm.mem_limit_mib = lim.get("mem")
            if svc in usage:
                sm.cpu_millicores = usage[svc]["cpu"]
                sm.mem_mib = usage[svc]["mem"]
                if sm.cpu_limit_millicores:
                    sm.cpu_ratio = sm.cpu_millicores / sm.cpu_limit_millicores
                if sm.mem_limit_mib:
                    sm.mem_ratio = sm.mem_mib / sm.mem_limit_mib
        return m

    def _logs(self, namespace: str, services: List[str]) -> Dict[str, ServiceLogs]:
        out: Dict[str, ServiceLogs] = {}
        err_re = re.compile(r"\b(ERROR|Exception|panic|fatal|timeout|refused|"
                            r"connection reset|5\d\d)\b", re.IGNORECASE)
        for svc in services:
            text = self._run(["logs", "-n", namespace, "-l", f"app={svc}",
                              f"--tail={self.log_tail}", "--all-containers",
                              "--prefix=false"], timeout=15)
            lines = [ln for ln in text.splitlines() if ln.strip()]
            errs = [ln for ln in lines if err_re.search(ln)]
            out[svc] = ServiceLogs(service=svc, error_count=len(errs),
                                   total_lines=len(lines), samples=[e[:200] for e in errs[:3]])
        return out

    def _apply_trace_signals(self, traces, metrics: Dict[str, ServiceMetrics]) -> None:
        """Per-service inbound latency/error from traces (preferred over ping)."""
        from .trace_sources import trace_latency_by_service
        by_svc = trace_latency_by_service(traces)
        for svc, sig in by_svc.items():
            sm = metrics.get(svc)
            if sm is not None:
                sm.trace_latency_ms = sig.get("latency_ms")
                sm.trace_error_pct = sig.get("error_pct")

    # -- active symptom probes -------------------------------------------------
    def _has_resource_anomaly(self, metrics: Dict[str, ServiceMetrics]) -> bool:
        """Probe-gating predicate: True only when a STRONG, self-explanatory
        cpu/mem/config signal already localizes the fault (so symptom probes can be
        skipped for speed). Deliberately does NOT include ``not_ready``: a pod that is
        merely not-ready is ambiguous — it can be pod-fail OR a network/disk fault that
        tripped the pod's readiness probe (e.g. a +1000ms net-delay makes the gRPC
        health check exceed its 1s timeout). Gating on not_ready would skip exactly the
        ping/disk probe that disambiguates it (rtt≈1.25s → network-delay), so a
        backend-only service's net fault would be mislabeled pod-fail. (v4 regression.)"""
        for m in metrics.values():
            if (m.cpu_ratio is not None and m.cpu_ratio >= 0.5) or \
               (m.mem_ratio is not None and m.mem_ratio >= 0.5) or \
               (m.cpu_limit_millicores is not None and m.cpu_limit_millicores <= 5):
                return True
        return False

    def _pod_by_service(self, pods: Dict[str, Any]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for p in (pods.get("items") or []):
            app = self._pod_app(p)
            name = (p.get("metadata") or {}).get("name", "")
            if not app or not name or app in out:
                continue
            if ((p.get("status") or {}).get("phase")) == "Running":
                out[app] = name
        return out

    def _exec(self, namespace: str, pod: str, remote: List[str], containers=None) -> Tuple[int, str]:
        """kubectl exec, trying the given container preference order.

        Default prefers sidecar-busybox (has ping). Disk probes pass the MAIN
        container first, since the IOChaos affects the app container's volume.
        """
        env = dict(os.environ); env["KUBECONFIG"] = self.kubeconfig
        for cargs in (containers or (["-c", "sidecar-busybox"], [])):
            try:
                r = subprocess.run(["kubectl", "exec", pod, "-n", namespace, *cargs, "--", *remote],
                                   capture_output=True, text=True, timeout=self.probe_timeout, env=env)
                out = (r.stdout or "") + (r.stderr or "")
                if "OCI runtime exec failed" in out or "executable file not found" in out or \
                   "container not found" in out or "not found in pod" in out:
                    continue
                return r.returncode, r.stdout
            except subprocess.TimeoutExpired:
                return 124, ""
            except Exception:
                return 1, ""
        return 1, ""

    def _parallel(self, items, fn):
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.probe_workers) as ex:
            list(ex.map(fn, items))

    def _network_probes(self, namespace: str, pod_by_svc: Dict[str, str],
                        metrics: Dict[str, ServiceMetrics]) -> None:
        def probe(item):
            svc, pod = item
            # 10 packets (not 4) for a stable loss% estimate -> lower variance.
            _, out = self._exec(namespace, pod, ["ping", "-c", "10", "-i", "0.2", "-W", "2",
                                                 self.ping_target])
            loss, rtt = None, None
            for line in out.splitlines():
                m = re.search(r"(\d+(?:\.\d+)?)%\s*packet loss", line)
                if m:
                    loss = float(m.group(1))
                m = re.search(r"(?:rtt|round-trip)[^=]*=\s*[\d.]+/([\d.]+)/", line)
                if m:
                    rtt = float(m.group(1))
            sm = metrics.get(svc)
            if sm is not None:
                if loss is not None:
                    sm.loss_pct = loss
                if rtt is not None:
                    sm.rtt_ms = rtt
        self._parallel(list(pod_by_svc.items()), probe)

    def _disk_probes(self, namespace: str, pod_by_svc: Dict[str, str],
                     metrics: Dict[str, ServiceMetrics]) -> None:
        # busybox `date` lacks ms precision, so detect slow disk by COMPLETION vs
        # TIMEOUT: IOChaos delays each IO by 5s, so a 10-write direct dd takes
        # ~50s and times out; a healthy pod completes in <2s.
        tmp = f"{self.disk_path}/rca_probe.tmp"
        script = f"dd if=/dev/zero of={tmp} bs=1M count=10 oflag=direct 2>/dev/null; rm -f {tmp}"

        def probe(item):
            svc, pod = item
            # Probe ONLY in sidecar-busybox: the v0.10.x app ("server") containers are
            # distroless (no shell -> exec fails), and the IOChaos is now injected into
            # sidecar-busybox (the disk-io template's containerNames), so the latency is
            # observable only there. A server-first order would read the non-chaos'd
            # server as healthy on the shell-bearing (Python/Node) services and miss it.
            rc, _ = self._exec(namespace, pod, ["sh", "-c", script], containers=(["-c", "sidecar-busybox"],))
            sm = metrics.get(svc)
            if sm is None:
                return
            if rc == 124:                       # timed out -> disk IO is being delayed
                sm.disk_write_ms = self.probe_timeout * 1000.0
            elif rc == 0:                       # completed quickly -> healthy disk
                sm.disk_write_ms = 200.0
            # else (exec failed) -> leave None
        self._parallel(list(pod_by_svc.items()), probe)

    def _events(self, events: Dict[str, Any]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for e in (events.get("items") or []):
            if e.get("type") == "Warning":
                out.append({
                    "reason": e.get("reason"),
                    "message": (e.get("message") or "")[:160],
                    "object": (e.get("involvedObject") or {}).get("name"),
                    "kind": (e.get("involvedObject") or {}).get("kind"),
                    "count": e.get("count"),
                })
        return out[-40:]
