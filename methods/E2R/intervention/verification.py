"""Independent post-action checks. Mitigation never implies defect elimination."""
from __future__ import annotations

from dataclasses import dataclass, replace

from rca_bench.recovery_oracle import WorkloadWindow
from .schema import number


def conjunction(values):
    values = list(values)
    if any(v == "fail" for v in values):
        return "fail"
    return "pass" if values and all(v == "pass" for v in values) else "unknown"


@dataclass(frozen=True)
class VerificationProtocol:
    required_windows: int = 3
    min_samples: int = 20
    error_margin: float = 0.01
    latency_multiplier: float = 1.2
    throughput_fraction: float = 0.8
    max_age: float = 120.0
    require_canary: bool = True
    latency_floor_ms: float = 0.0

    def __post_init__(self):
        if (self.required_windows < 1 or self.min_samples < 1
                or not all(number(x) for x in (self.error_margin, self.latency_multiplier, self.throughput_fraction))
                or self.error_margin < 0
                or self.latency_multiplier <= 0 or not 0 < self.throughput_fraction <= 1
                or not number(self.latency_floor_ms) or self.latency_floor_ms < 0
                or not number(self.max_age) or self.max_age <= 0):
            raise ValueError("invalid business verification protocol")


def business_windows(baseline: WorkloadWindow, windows: list[WorkloadWindow], *,
                     completed_at: float, evaluated_at: float, protocol: VerificationProtocol) -> dict:
    def measured_at(w):
        return w.end_time if w.measurement_completed_at is None else w.measurement_completed_at
    def measurable(w):
        return (type(w.samples) is int and w.samples >= protocol.min_samples
                and all(number(x) for x in (w.throughput_rps, w.error_rate, w.p95_ms, w.start_time, w.end_time))
                and w.throughput_rps >= 0 and 0 <= w.error_rate <= 1 and w.p95_ms >= 0
                and w.start_time < w.end_time and number(measured_at(w)) and measured_at(w) >= w.end_time
                and bool(w.source_id) and bool(w.window_id))
    baseline_ok = (measurable(baseline) and baseline.throughput_rps > 0 and baseline.p95_ms > 0
                   and number(completed_at) and number(evaluated_at)
                   and measured_at(baseline) <= completed_at <= evaluated_at)
    rows, seen, previous_end = [], {baseline.window_id}, completed_at
    for w in windows:
        valid = (baseline_ok and measurable(w) and w.window_id not in seen
                 and w.source_id == baseline.source_id and w.start_time >= previous_end
                 and measured_at(w) <= evaluated_at and evaluated_at - w.end_time <= protocol.max_age)
        if valid:
            previous_end = w.end_time
        seen.add(w.window_id)
        state = "unknown"
        if valid and type(w.state_canary_ok) in (bool, type(None)) and (not protocol.require_canary or type(w.state_canary_ok) is bool):
            ok = (w.error_rate <= baseline.error_rate + protocol.error_margin
                  and w.p95_ms <= max(baseline.p95_ms * protocol.latency_multiplier, protocol.latency_floor_ms)
                  and w.throughput_rps >= baseline.throughput_rps * protocol.throughput_fraction
                  and w.state_canary_ok is not False)
            state = "pass" if ok else "fail"
        rows.append({"window_id": w.window_id, "status": state, "valid": valid})
    enough = len(rows) >= protocol.required_windows
    status = conjunction(r["status"] for r in rows[-protocol.required_windows:]) if enough else "unknown"
    if not baseline_ok or any(not r["valid"] for r in rows):
        status = "unknown"
    return {"status": status, "baseline_valid": baseline_ok, "windows": rows,
            "evaluated_at": evaluated_at, "execution_completed_at": completed_at,
            "required_windows": protocol.required_windows,
            "sustained_seconds": (windows[-1].end_time - windows[-protocol.required_windows].start_time)
            if status == "pass" else None}


def verification_package(*, field_status: str, controller_status: str, business: dict,
                         non_target: dict[str, dict], mechanism_status: str = "unknown") -> dict:
    legal = {"pass", "fail", "unknown"}
    if any(v not in legal for v in (field_status, controller_status, mechanism_status, business.get("status"))):
        raise ValueError("invalid independent verification status")
    safety = conjunction(v["status"] for v in non_target.values())
    mitigation = conjunction([field_status, controller_status, business["status"], safety])
    return {"write_semantics": field_status, "controller_convergence": controller_status,
            "business": business, "non_target": non_target, "non_target_safety": safety,
            "mitigation": mitigation, "mechanism_eliminated": mechanism_status,
            "fault_elimination": conjunction([mitigation, mechanism_status])}


def execution_overlap_windows(baseline, windows, *, started_at, completed_at, evaluated_at, protocol):
    """Evaluate all fixed arrival windows overlapping execution, including tails.

    A cohort can include nearby pre/post requests; it is not an identified causal
    effect of the write. Complete temporal coverage is required for a safe label.
    """
    covers = (bool(windows) and all(number(w.start_time) and number(w.end_time) for w in windows)
              and number(started_at) and number(completed_at) and started_at <= completed_at
              and windows[0].start_time <= started_at < windows[0].end_time
              and windows[-1].start_time < completed_at <= windows[-1].end_time
              and all(w.start_time < completed_at and w.end_time > started_at for w in windows)
              and all(abs(a.end_time-b.start_time)<1e-6 for a,b in zip(windows,windows[1:])))
    if not covers:
        return {"status":"unknown","coverage_complete":False,"evaluated_at":evaluated_at}
    # This is a retrospective question about the bounded execution interval.
    # Post-action recovery windows still retain their original freshness limit.
    retrospective_age = max(protocol.max_age,evaluated_at-windows[0].start_time)
    result = business_windows(baseline,windows,completed_at=windows[0].start_time,evaluated_at=evaluated_at,
                              protocol=replace(protocol,required_windows=len(windows),max_age=retrospective_age))
    return {**result,"coverage_complete":True,"action_started_at":started_at,"action_completed_at":completed_at,
            "scope":"all arrival cohorts overlapping execution; includes adjacent requests"}
