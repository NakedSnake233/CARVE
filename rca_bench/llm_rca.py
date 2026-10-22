"""LLM-driven RCA adapter — drives plan2remed's diagnosis with an external LLM.

The configured OpenCode Go models use either OpenAI-compatible chat completions
or Anthropic-compatible messages. This adapter does not rely on tool calling; it
prompts the model to return a JSON object in message text and parses that. It consumes the SAME oracle-hidden
``MultimodalContext`` every other adapter sees and returns a ranked ``RCAResult``
+ predicted category, so it plugs into the decoupled full-lifecycle harness
unchanged (``--rca-method llmrca``); remediation stays the report-driven,
RCA-bounded stage. On any LLM failure it falls back to the deterministic backbone
(never raises, per the BaseRCA contract) and records that it fell back.

Model/protocol/endpoint come from ``rca2Remed/llm_rca_config.json`` (swap via ``active`` /
env ``RCA_LLM_MODEL_NAME``); ``LLM_BASE_URL`` / ``LLM_API_KEY`` / ``RCA_LLM_MODEL``
env vars override. A per-incident JSONL trace is appended to ``RCA_LLM_LOG``
(default ``conversations/llmrca_calls.jsonl``) so LLM-vs-fallback usage is auditable.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from typing import Dict, List, Optional

from .interface import BaseRCA, MultimodalContext, RCAResult, RootCauseCandidate
from .llm_transport import build_request, parse_response, session_id

CATS = ["cpu-stress", "memory-stress", "disk-io", "network-delay",
        "network-loss", "pod-fail", "pod-config-error"]

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_cfg() -> Dict:
    path = os.environ.get("RCA_LLM_CONFIG", os.path.join(_HERE, "..", "llm_rca_config.json"))
    cfg = json.load(open(path))
    name = os.environ.get("RCA_LLM_MODEL_NAME", cfg.get("active"))
    m = dict(cfg["models"][name])
    out = dict(cfg.get("request", {}))
    out.update(m)
    out["_name"] = name
    out["base_url"] = os.environ.get("LLM_BASE_URL", out["base_url"])
    out["api_key"] = os.environ.get("LLM_API_KEY", out["api_key"])
    out["model"] = os.environ.get("RCA_LLM_MODEL", out["model"])
    return out


def _norm_cat(c: Optional[str]) -> Optional[str]:
    if not c:
        return None
    c = str(c).strip().lower().replace("_", "-").replace(" ", "-")
    if c in CATS:
        return c
    alias = {
        "cpu": "cpu-stress", "cpu-stress": "cpu-stress", "cpu-hog": "cpu-stress",
        "memory": "memory-stress", "mem": "memory-stress", "mem-stress": "memory-stress",
        "memory-leak": "memory-stress", "oom": "memory-stress",
        "disk": "disk-io", "disk-io": "disk-io", "io": "disk-io", "diskio": "disk-io",
        "network-delay": "network-delay", "net-delay": "network-delay",
        "latency": "network-delay", "delay": "network-delay",
        "network-loss": "network-loss", "net-loss": "network-loss",
        "packet-loss": "network-loss", "loss": "network-loss",
        "pod-fail": "pod-fail", "pod-failure": "pod-fail", "pod-kill": "pod-fail",
        "process-kill": "pod-fail", "crash": "pod-fail",
        "pod-config-error": "pod-config-error", "config-error": "pod-config-error",
        "config": "pod-config-error", "misconfiguration": "pod-config-error",
    }
    if c in alias:
        return alias[c]
    for k in CATS:                      # substring fallback
        if k in c:
            return k
    return c


def _sig_line(m, log, ev: int) -> str:
    parts: List[str] = []
    if m is not None:
        if m.cpu_ratio is not None and m.cpu_ratio >= 0.05:
            parts.append(f"cpu_ratio={m.cpu_ratio:.2f}")
        if m.mem_ratio is not None and m.mem_ratio >= 0.05:
            parts.append(f"mem_ratio={m.mem_ratio:.2f}")
        if m.not_ready:
            parts.append(f"not_ready_pods={m.not_ready}")
        if m.restarts:
            parts.append(f"restarts={m.restarts}")
        if m.cpu_limit_millicores is not None and m.cpu_limit_millicores <= 5:
            parts.append(f"cpu_limit_millicores={m.cpu_limit_millicores:.0f}")
        if m.rtt_ms is not None:
            parts.append(f"ping_rtt_ms={m.rtt_ms:.0f}")
        if m.loss_pct is not None:
            parts.append(f"ping_loss_pct={m.loss_pct:.0f}")
        if m.disk_write_ms is not None:
            parts.append(f"disk_write_ms={m.disk_write_ms:.0f}")
        if m.trace_latency_ms is not None:
            parts.append(f"trace_inbound_p99_ms={m.trace_latency_ms:.0f}")
        if m.trace_error_pct is not None:
            parts.append(f"trace_inbound_err_pct={m.trace_error_pct:.0f}")
    if log is not None and log.error_count:
        parts.append(f"log_errors={log.error_count}")
    if ev:
        parts.append(f"k8s_warning_events={ev}")
    return ", ".join(parts) if parts else "(no anomaly signals)"


_SYSTEM = (
    "You are a microservice root-cause-analysis engine for a "
    "deployment on Kubernetes. For ONE incident you receive each "
    "candidate service's telemetry and active-probe signals collected during the "
    "incident window. Identify the single most likely root-cause SERVICE, rank the "
    "five most likely, and classify the fault into exactly one category. Reply ONLY "
    "with compact JSON, no prose.\n"
    "Signal meanings: cpu_ratio / mem_ratio = usage over the pod limit (>=0.5 = "
    "stress); not_ready_pods = pods running but failing health checks (pod-fail); "
    "cpu_limit_millicores present and tiny (<=5) = misconfigured resource limit "
    "(pod-config-error); ping_rtt_ms = egress latency probe (fleet baseline ~250ms; "
    ">=600 = network-delay); ping_loss_pct = egress packet-loss probe (baseline "
    "~10%; ~50% = network-loss); disk_write_ms = disk write-latency probe (baseline "
    "~200ms; multi-second = disk-io); trace_inbound_p99_ms / trace_inbound_err_pct = "
    "inter-service latency/errors from distributed tracing. Reason relative to the "
    "fleet: pick the service that stands out, not the whole cluster. A service whose "
    "inbound trace errors/latency are high ONLY because a downstream callee it CALLS "
    "is itself failed is a cascade VICTIM, not the root — blame the deepest failing "
    "service along the call graph."
)


def _build_user(ctx: MultimodalContext) -> str:
    ev_by_svc: Dict[str, int] = {}
    for e in ctx.k8s_events:
        obj = e.get("object") or ""
        for s in ctx.services:
            if obj.startswith(s):
                ev_by_svc[s] = ev_by_svc.get(s, 0) + int(e.get("count") or 1)
    lines = [f"{s}: {_sig_line(ctx.metrics.get(s), ctx.logs.get(s), ev_by_svc.get(s, 0))}"
             for s in ctx.services]
    # compact call graph (merged static topology + observed trace edges)
    edges = set(ctx.topology) | {(e.src, e.dst) for e in ctx.traces}
    graph = ", ".join(f"{a}->{b}" for a, b in sorted(edges)) or "(unknown)"
    return (
        f"Application: {ctx.env}; namespace: {ctx.namespace}\n"
        f"Incident alert: {ctx.incident_summary or 'a service in the namespace is unhealthy'}\n"
        f"Call graph (caller->callee): {graph}\n"
        f"Candidate services and signals:\n" + "\n".join(lines) + "\n\n"
        f"Fault categories (use EXACTLY one): {', '.join(CATS)}.\n"
        'Return JSON exactly: {"ranking": ["<top1>","<top2>","<top3>","<top4>","<top5>"], '
        '"category": "<one category>", "reason": "<one short sentence>"}. '
        "Use service names verbatim from the candidate list."
    )


def _call_llm(cfg: Dict, system: str, user: str, telemetry: Optional[Dict] = None) -> str:
    """Record each request's measured time and provider usage, including retries.

    A failed request may still be billed: absent usage is unknown, never zero.
    No credentials, endpoint URLs, or prompt content are copied to telemetry.
    """
    if telemetry is None:
        telemetry = {}
    telemetry.update({"model": cfg["model"], "attempts": []})
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    req, _ = build_request(cfg, messages, request_session=session_id("rca-live"))
    last = None
    n = cfg.get("max_retries", 8)
    for k in range(n):
        attempt = {"attempt": k + 1, "success": False, "usage": None}
        telemetry["attempts"].append(attempt)
        started = time.perf_counter()
        try:
            r = urllib.request.urlopen(req, timeout=cfg.get("timeout_s", 120)).read()
            response = json.loads(r)
            parsed = parse_response(response, cfg.get("api_style", "openai"))
            attempt["usage"] = parsed["usage"]
            content = parsed["response_text"]
            attempt["success"] = True
            return content
        except Exception as e:
            last = e
            attempt["error_type"] = type(e).__name__
        finally:
            attempt["request_seconds"] = time.perf_counter() - started
        if not attempt["success"]:
            if k < n - 1:
                retry_started = time.perf_counter()
                time.sleep(min(5 + 5 * k, 30))   # patient: ride out transient gateway 503 bursts
                attempt["retry_wait_seconds"] = time.perf_counter() - retry_started
    raise RuntimeError(f"LLM call failed after retries: {last}")


def _parse(txt: str):
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        return [], None
    raw = m.group(0)
    try:
        d = json.loads(raw)
    except Exception:
        try:
            d = json.loads(re.sub(r",\s*([}\]])", r"\1", raw.replace("'", '"')))
        except Exception:
            return [], None
    rk = [str(x).strip() for x in (d.get("ranking") or []) if str(x).strip()]
    return rk, _norm_cat(d.get("category"))


def _log(rec: Dict) -> None:
    path = os.environ.get("RCA_LLM_LOG",
                          os.path.join(_HERE, "..", "conversations", "llmrca_calls.jsonl"))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


class LLMRCA(BaseRCA):
    """Drives RCA ranking + category with an external LLM (JSON-content path)."""
    name = "llmrca"

    def __init__(self):
        self.last_diagnostics = {}
        try:
            self.cfg = _load_cfg()
        except Exception as e:        # config missing -> deterministic-only
            self.cfg = None
            print(f"[llmrca] config load failed, will fall back to deterministic: {e}", flush=True)

    def _fallback(self, ctx: MultimodalContext, reason: str) -> RCAResult:
        from .adapters import _anomaly_scores, _infer_category
        scores = _anomaly_scores(ctx)
        ranking = [RootCauseCandidate(service=s, score=round(sc, 4),
                                      failure_category=_infer_category(ctx, s))
                   for s, sc in scores.items()]
        ranking.sort(key=lambda c: c.score, reverse=True)
        top = ranking[0] if ranking else None
        self.last_diagnostics.update({"used_llm": False, "fallback_reason": reason})
        _log({**self.last_diagnostics,
              "pred_service": top.service if top else None,
              "pred_category": top.failure_category if top else None})
        return RCAResult(ranking=ranking, method=self.name,
                         predicted_category=(top.failure_category if top else None))

    def diagnose(self, ctx: MultimodalContext) -> RCAResult:
        self.last_diagnostics = {"model": self.cfg["model"] if self.cfg else None,
                                 "attempts": [], "used_llm": False}
        if not self.cfg:
            return self._fallback(ctx, "no-config")
        try:
            user = _build_user(ctx)
            txt = _call_llm(self.cfg, _SYSTEM, user, self.last_diagnostics)
        except Exception as e:
            return self._fallback(ctx, f"llm-error: {e}")
        ranking_names, category = _parse(txt)
        # snap returned names to real candidates (case-insensitive exact)
        cset = {s.lower(): s for s in ctx.services}
        snapped, seen = [], set()
        for nm in ranking_names:
            real = cset.get(nm.lower(), nm)
            if real in ctx.services and real not in seen:
                snapped.append(real)
                seen.add(real)
        if not snapped:
            return self._fallback(ctx, "empty-or-unparseable-ranking")
        # build ranking: LLM order first (score by descending position), then the
        # remaining candidates at score 0 so the list is complete for AC@k.
        n = len(snapped)
        ranking = [RootCauseCandidate(service=s, score=float(n - i),
                                      failure_category=category)
                   for i, s in enumerate(snapped)]
        for s in ctx.services:
            if s not in seen:
                ranking.append(RootCauseCandidate(service=s, score=0.0,
                                                  failure_category=None))
        self.last_diagnostics["used_llm"] = True
        _log({**self.last_diagnostics, "pred_service": snapped[0], "pred_category": category,
              "top5": snapped[:5]})
        return RCAResult(ranking=ranking, method=self.name, predicted_category=category)
