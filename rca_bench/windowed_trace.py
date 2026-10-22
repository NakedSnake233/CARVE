"""Jaeger trace source bounded to a declared incident window."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from .context_provider import TraceSource
from .trace_sources import aggregate_edges


def _seconds(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


class WindowedJaegerTraceSource(TraceSource):
    def __init__(self, base_url: str, *, per_service_limit: int = 100,
                 timeout_s: int = 8):
        self.base_url = base_url.rstrip("/")
        self.per_service_limit = per_service_limit
        self.timeout_s = timeout_s
        self.start: Optional[float] = None
        self.end: Optional[float] = None
        self.candidates: Optional[set[str]] = None
        self.last_coverage = {}

    def configure(self, start, end, candidates: list[str]) -> None:
        self.start, self.end = _seconds(start), _seconds(end)
        if self.end <= self.start:
            raise ValueError("Jaeger window end must follow start")
        self.candidates = set(candidates)

    def _get(self, path: str) -> dict:
        try:
            with urllib.request.urlopen(self.base_url + path, timeout=self.timeout_s) as response:
                return json.load(response)
        except Exception:
            return {}

    def edges(self, namespace: str, env: str):
        if self.start is None or self.end is None or self.candidates is None:
            raise ValueError("window and candidate set must be configured")
        services = [s for s in self._get("/api/services").get("data") or []
                    if s in self.candidates]
        traces = []
        accepted_spans = 0
        for service in services:
            query = urllib.parse.urlencode({"service": service,
                                            "start": int(self.start * 1_000_000),
                                            "end": int(self.end * 1_000_000),
                                            "limit": self.per_service_limit})
            response = self._get("/api/traces?" + query)
            for trace in response.get("data") or []:
                spans = []
                for span in trace.get("spans") or []:
                    start = (span.get("startTime") or 0) / 1_000_000.0
                    duration = (span.get("duration") or 0) / 1_000_000.0
                    if self.start <= start and start + max(0, duration) <= self.end:
                        spans.append(span)
                if spans:
                    accepted_spans += len(spans)
                    traces.append({**trace, "spans": spans})
        self.last_coverage = {"queried_services": len(services),
                              "accepted_traces": len(traces),
                              "accepted_spans": accepted_spans,
                              "start": self.start, "end": self.end}
        return aggregate_edges(traces)
