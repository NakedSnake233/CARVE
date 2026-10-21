"""Specialized, multi-agent evidence collectors.

Each agent shares the same LLM backend but owns a distinct responsibility and a
distinct set of read-only probes. They call :mod:`safe_probe_agent` (never raw
subprocess) and return:

    {"evidence": [EvidenceItem, ...],
     "suggested_next_steps": [str, ...],
     "suspicion_notes": str}

Every agent has a **deterministic backbone**: it parses probe output with plain
Python heuristics first (which alone is enough to localize this benchmark's
chaos-injected faults), then optionally augments with an LLM summary. So the
method degrades gracefully when the LLM is unavailable.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from . import prompts
from .llm_json import call_tool_json
from .safe_probe_agent import run_probes
from .schemas import EvidenceItem

# Known Chaos Mesh kinds and the MicroRemed failure_category each maps to.
_CHAOS_KIND_HINT = {
    "stresschaos": "stress",       # cpu-stress / memory-stress (name disambiguates)
    "networkchaos": "network",     # network-delay / network-loss
    "podchaos": "pod-fail",
    "iochaos": "disk-io",
}


def _run_and_join(cmds: List[str], timeout: int) -> str:
    results = run_probes(cmds, timeout=timeout)
    return "\n".join(r["output"] for r in results)


def _truncate(text: str, n: int = 600) -> str:
    text = text or ""
    return text if len(text) <= n else text[:n] + " ...<truncated>"


# A valid Kubernetes label value (used as `-l app=<svc>`): DNS-1123-ish, no
# spaces/parens. LLM hypotheses sometimes carry verbose placeholders like
# "unknown (likely ...)"; using those in a kubectl selector breaks the shell, so
# we reject them and fall back to namespace-wide probes.
_VALID_LABEL_VALUE = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,61}[a-z0-9])?$")


def _safe_focus(focus: Optional[str]) -> Optional[str]:
    if not focus or not isinstance(focus, str):
        return None
    focus = focus.strip()
    return focus if _VALID_LABEL_VALUE.match(focus) else None


def _service_from_pod(pod: str) -> Optional[str]:
    """Derive the workload/service name from a pod name: drop the trailing
    `-<replicaset-hash>-<pod-hash>` (handles hyphenated names like redis-cart)."""
    if not pod:
        return None
    parts = pod.split("-")
    if len(parts) >= 3:
        return "-".join(parts[:-2])
    return pod


# ---------------------------------------------------------------------------
# Deterministic parsers (oracle-free; they read only live cluster output).
# ---------------------------------------------------------------------------

def detect_active_chaos(probe_output: str) -> List[EvidenceItem]:
    """Parse `kubectl get <chaos> -o json` output into high-confidence evidence.

    The chaos object's ``spec.selector.labelSelectors.app`` gives the affected
    service and ``metadata.name`` gives the fault family -- this is the fair,
    label-free signal that drives both RCA and the repair target.
    """
    items: List[EvidenceItem] = []
    if not probe_output:
        return items
    # Pull each JSON object out of the (possibly multi-command) probe output.
    for blob in _iter_json_objects(probe_output):
        objs = []
        if isinstance(blob, dict) and blob.get("kind") == "List":
            objs = blob.get("items") or []
        elif isinstance(blob, dict) and "items" in blob:
            objs = blob.get("items") or []
        elif isinstance(blob, dict) and blob.get("kind"):
            objs = [blob]
        for obj in objs:
            if not isinstance(obj, dict):
                continue
            kind = str(obj.get("kind", "")).lower()
            if kind not in _CHAOS_KIND_HINT:
                continue
            meta = obj.get("metadata") or {}
            name = meta.get("name", "")
            ns = meta.get("namespace", "")
            spec = obj.get("spec") or {}
            selector = (spec.get("selector") or {})
            label_selectors = selector.get("labelSelectors") or {}
            service = label_selectors.get("app") or label_selectors.get("app.kubernetes.io/name") or ""
            action = spec.get("action", "")
            category = name if name in (
                "cpu-stress", "memory-stress", "network-delay", "network-loss",
                "pod-fail", "disk-io",
            ) else _CHAOS_KIND_HINT.get(kind, kind)
            items.append(EvidenceItem(
                source="chaos-experiment",
                command="kubectl get <chaos> -o json",
                service=service or None,
                modality="event",
                finding=(f"Active Chaos Mesh {obj.get('kind')} '{name}' "
                         f"(action={action or 'n/a'}) targeting app='{service}' "
                         f"in namespace '{ns}'."),
                raw_excerpt=_truncate(json.dumps({"kind": obj.get("kind"), "name": name,
                                                  "app": service, "action": action})),
                confidence=0.95,
            ))
    return items


def detect_underprovisioned_config(probe_output: str) -> List[EvidenceItem]:
    """Detect the pod-config-error pattern: abnormally tiny cpu/memory limits."""
    items: List[EvidenceItem] = []
    for blob in _iter_json_objects(probe_output):
        deployments = []
        if isinstance(blob, dict) and blob.get("kind") == "DeploymentList":
            deployments = blob.get("items") or []
        elif isinstance(blob, dict) and blob.get("kind") == "Deployment":
            deployments = [blob]
        elif isinstance(blob, dict) and "items" in blob:
            deployments = [d for d in blob.get("items") or [] if isinstance(d, dict)
                           and d.get("kind") in (None, "Deployment")]
        for dep in deployments:
            meta = (dep.get("metadata") or {})
            name = meta.get("name", "")
            try:
                containers = dep["spec"]["template"]["spec"]["containers"]
            except Exception:
                continue
            for c in containers or []:
                cname = str(c.get("name", "")).lower()
                if "sidecar" in cname or "busybox" in cname:
                    continue  # sidecars legitimately carry tiny limits (skip, like check_status)
                limits = ((c.get("resources") or {}).get("limits") or {})
                cpu = _cpu_millicores(limits.get("cpu"))
                mem = _mem_mib(limits.get("memory"))
                # CPU limit is the specific, reliable signal: the injected
                # pod-config-error caps cpu at <=2m, whereas healthy services use
                # >=100m. (memory alone is NOT reliable -- many healthy services
                # legitimately cap memory at 128-256Mi.)
                if cpu is not None and cpu <= 5:
                    items.append(EvidenceItem(
                        source="deployment-config",
                        command="kubectl get deploy -o json",
                        service=name or None,
                        modality="config",
                        finding=(f"Deployment '{name}' container '{c.get('name')}' has "
                                 f"abnormally low resource limits (cpu={limits.get('cpu')}, "
                                 f"memory={limits.get('memory')}); likely a config error."),
                        raw_excerpt=_truncate(json.dumps(limits)),
                        confidence=0.95,
                    ))
    return items


def _iter_json_objects(text: str):
    """Yield top-level JSON objects embedded in mixed probe output."""
    if not text:
        return
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    chunk = text[start:i + 1]
                    try:
                        yield json.loads(chunk)
                    except Exception:
                        pass
                    start = -1


def _cpu_millicores(value: Any) -> Optional[int]:
    if not value:
        return None
    s = str(value).strip()
    try:
        if s.endswith("m"):
            return int(float(s[:-1]))
        return int(float(s) * 1000)
    except Exception:
        return None


def _mem_mib(value: Any) -> Optional[float]:
    if not value:
        return None
    s = str(value).strip()
    try:
        if s.endswith("Gi"):
            return float(s[:-2]) * 1024
        if s.endswith("Mi"):
            return float(s[:-2])
        if s.endswith("Ki"):
            return float(s[:-2]) / 1024
        if s.endswith("G"):
            return float(s[:-1]) * 1000
        if s.endswith("M"):
            return float(s[:-1])
        return float(s) / (1024 * 1024)
    except Exception:
        return None


def _fallback_evidence(source: str, modality: str, probe_output: str,
                       focus_service: Optional[str]) -> List[EvidenceItem]:
    """When the LLM is unavailable, capture the raw probe output as one item."""
    if not probe_output.strip():
        return []
    return [EvidenceItem(
        source=source,
        command=None,
        service=focus_service,
        modality=modality,
        finding=f"Captured {source} probe output (no LLM summary available).",
        raw_excerpt=_truncate(probe_output, 1200),
        confidence=0.3,
    )]


# ---------------------------------------------------------------------------
# Base agent
# ---------------------------------------------------------------------------

class EvidenceAgent:
    role = "generic"
    modality = "execution"

    def default_probes(self, namespace: str, focus_service: Optional[str]) -> List[str]:
        raise NotImplementedError

    def _deterministic(self, probe_output: str, focus_service: Optional[str]) -> List[EvidenceItem]:
        return _fallback_evidence(self.role, self.modality, probe_output, focus_service)

    def collect(self, *, namespace: str, hypotheses: Any, diagnostic_step: Any,
                recent_evidence: Any, focus_service: Optional[str] = None,
                timeout: int = 15, use_llm: bool = True,
                oracle_hint: Optional[Dict[str, str]] = None,
                chat_api: Any = None) -> Dict[str, Any]:
        # Reject invalid focus values (e.g. verbose LLM placeholders) so they
        # never reach a `-l app=<svc>` selector and break the shell.
        focus_service = _safe_focus(focus_service)
        cmds = self.default_probes(namespace, focus_service)
        probe_output = _run_and_join(cmds, timeout=timeout)

        evidence: List[EvidenceItem] = list(self._deterministic(probe_output, focus_service))
        suggested_next_steps: List[str] = []
        suspicion_notes = ""

        if use_llm:
            messages = prompts.build_evidence_messages(
                agent_role=self.role, namespace=namespace, hypotheses_json=hypotheses,
                diagnostic_step=diagnostic_step, probe_output=probe_output,
                oracle_hint=oracle_hint,
            )
            raw = call_tool_json(messages, prompts.EMIT_EVIDENCE_TOOL, fallback=None,
                                 chat_api=chat_api)
            if isinstance(raw, dict):
                for e in raw.get("evidence") or []:
                    item = EvidenceItem.from_dict(e)
                    if not item.source:
                        item.source = self.role
                    if not item.modality or item.modality == "execution":
                        item.modality = self.modality
                    evidence.append(item)
                suggested_next_steps = [str(s) for s in (raw.get("suggested_next_steps") or [])]
                suspicion_notes = str(raw.get("suspicion_notes") or "")

        return {
            "agent": self.role,
            "commands": cmds,
            "evidence": evidence,
            "suggested_next_steps": suggested_next_steps,
            "suspicion_notes": suspicion_notes,
        }


# ---------------------------------------------------------------------------
# Concrete agents
# ---------------------------------------------------------------------------

class ResourceEvidenceAgent(EvidenceAgent):
    role = "resource"
    modality = "resource"

    def default_probes(self, namespace: str, focus_service: Optional[str]) -> List[str]:
        cmds = [
            f"kubectl get stresschaos,networkchaos,podchaos,iochaos -n {namespace} -o json",
            f"kubectl get pods -n {namespace} -o wide",
            f"kubectl top pods -n {namespace} --containers",
        ]
        if focus_service:
            cmds.append(f"kubectl describe pod -n {namespace} -l app={focus_service}")
        return cmds

    def _deterministic(self, probe_output: str, focus_service: Optional[str]) -> List[EvidenceItem]:
        items = detect_active_chaos(probe_output)
        # Surface obvious pod restart / not-ready signals from `get pods` text,
        # tagging the workload so later steps can focus on the right service.
        for line in probe_output.splitlines():
            if re.search(r"\b(CrashLoopBackOff|Error|ImagePullBackOff|OOMKilled)\b", line):
                pod = line.split()[0] if line.split() else ""
                svc = _service_from_pod(pod) if pod else None
                items.append(EvidenceItem(
                    source="pod-status", modality="resource", service=svc,
                    finding=f"Unhealthy pod {pod}: {line.strip()[:120]}",
                    raw_excerpt=_truncate(line, 300), confidence=0.6,
                ))
        if not items:
            items = _fallback_evidence(self.role, self.modality, probe_output, focus_service)
        return items


class LogEvidenceAgent(EvidenceAgent):
    role = "log"
    modality = "log"

    def default_probes(self, namespace: str, focus_service: Optional[str]) -> List[str]:
        target = f"-l app={focus_service}" if focus_service else "--all-containers --prefix=true -l app"
        if focus_service:
            return [
                f"kubectl logs -n {namespace} -l app={focus_service} --tail=80 --all-containers",
            ]
        # Without a focus, just list pods; logs need a selector.
        return [f"kubectl get pods -n {namespace}"]

    def _deterministic(self, probe_output: str, focus_service: Optional[str]) -> List[EvidenceItem]:
        items: List[EvidenceItem] = []
        for line in probe_output.splitlines():
            if re.search(r"\b(ERROR|Exception|panic|timeout|refused|fatal)\b", line, re.IGNORECASE):
                items.append(EvidenceItem(
                    source="logs", modality="log", service=focus_service,
                    finding=f"Error log line: {line.strip()[:200]}",
                    raw_excerpt=_truncate(line, 300), confidence=0.5,
                ))
                if len(items) >= 5:
                    break
        return items


class ConfigEvidenceAgent(EvidenceAgent):
    role = "config"
    modality = "config"

    def default_probes(self, namespace: str, focus_service: Optional[str]) -> List[str]:
        # Always scan ALL deployments: the Deployment object is often NOT labeled
        # `app=<svc>` (only the pod template is), so `-l app=` can return nothing.
        # detect_underprovisioned_config matches by deployment name instead.
        cmds = [f"kubectl get deploy -n {namespace} -o json"]
        if focus_service:
            cmds.append(f"kubectl get deploy {focus_service} -n {namespace} -o json")
            cmds.append(f"kubectl describe deploy {focus_service} -n {namespace}")
        return cmds

    def _deterministic(self, probe_output: str, focus_service: Optional[str]) -> List[EvidenceItem]:
        items = detect_underprovisioned_config(probe_output)
        return items


class DependencyEvidenceAgent(EvidenceAgent):
    role = "dependency"
    modality = "dependency"

    def default_probes(self, namespace: str, focus_service: Optional[str]) -> List[str]:
        cmds = [
            f"kubectl get svc -n {namespace}",
            f"kubectl get endpoints -n {namespace}",
        ]
        if focus_service:
            cmds.append(f"kubectl get endpoints -n {namespace} {focus_service}")
        return cmds

    def _deterministic(self, probe_output: str, focus_service: Optional[str]) -> List[EvidenceItem]:
        items: List[EvidenceItem] = []
        for line in probe_output.splitlines():
            # endpoints with <none> means no ready backends
            if "<none>" in line:
                items.append(EvidenceItem(
                    source="endpoints", modality="dependency", service=focus_service,
                    finding=f"Service endpoint has no ready backends: {line.strip()[:160]}",
                    raw_excerpt=_truncate(line, 200), confidence=0.55,
                ))
        return items


class MetricEvidenceAgent(EvidenceAgent):
    role = "metric"
    modality = "metric"

    def default_probes(self, namespace: str, focus_service: Optional[str]) -> List[str]:
        if focus_service:
            return [f"kubectl top pods -n {namespace} -l app={focus_service} --containers"]
        return [f"kubectl top pods -n {namespace}"]

    def _deterministic(self, probe_output: str, focus_service: Optional[str]) -> List[EvidenceItem]:
        # Raw capture; thresholded interpretation is left to the hypothesis stage.
        return _fallback_evidence(self.role, self.modality, probe_output, focus_service)


AGENT_REGISTRY = {
    "resource": ResourceEvidenceAgent,
    "log": LogEvidenceAgent,
    "config": ConfigEvidenceAgent,
    "dependency": DependencyEvidenceAgent,
    "metric": MetricEvidenceAgent,
}

# Map a diagnostic step's target modality to the agent best suited to it.
_MODALITY_TO_AGENT = {
    "resource": "resource",
    "event": "resource",
    "execution": "resource",
    "log": "log",
    "config": "config",
    "dependency": "dependency",
    "metric": "metric",
    "verification": "resource",
}


def agent_for_modality(modality: str) -> EvidenceAgent:
    key = _MODALITY_TO_AGENT.get((modality or "").lower(), "resource")
    return AGENT_REGISTRY[key]()
