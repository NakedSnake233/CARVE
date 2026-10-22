"""Typed, JSON-serializable schemas for PlanRCAThinkRemed.

Standard-library ``dataclasses`` only (no pydantic, per MicroRemed dependency
constraints). Every schema:

* is JSON serializable via :func:`to_dict` / :func:`to_json`;
* can be reconstructed from a (possibly partial / messy) LLM dict via
  ``from_dict`` -- unknown keys are dropped, missing keys fall back to safe
  defaults, and scalar types are coerced. This is what lets the rest of the
  pipeline never crash on malformed model output.

No MicroRemed / kubernetes / httpx imports here so the offline tests can import
this module on a bare Python install.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Controlled vocabularies (kept as plain tuples so they are easy to validate
# against and to embed in prompts).
# ---------------------------------------------------------------------------

MODALITIES = (
    "resource",
    "log",
    "metric",
    "config",
    "dependency",
    "event",
    "execution",
    "verification",
)

REPAIRABILITY = ("unknown", "repairable", "unsafe", "needs_more_evidence")

RISK_LEVELS = ("low", "medium", "high")
EVIDENCE_BASES = ("observed", "prediction_only", "none")
ROLLBACK_POLICIES = (
    "reapply_captured_object",
    "restore_captured_container_resources",
    "none",
)

# Allowed repair action types (see repair_planner_agent / safety_guard).
ACTION_TYPES = (
    "PatchDeploymentEnv",
    "PatchDeploymentImage",
    "PatchServiceSelector",
    "PatchServicePort",
    "PatchProbe",
    "ScaleDeployment",
    "RestartSinglePodOrDeployment",
    "RemoveInjectedStress",
    "RecoverNetworkFault",
    "RecoverPodFailure",
    "RecoverConfigError",
    "WaitAndVerify",
)


# ---------------------------------------------------------------------------
# Small, defensive coercion helpers.
# ---------------------------------------------------------------------------

def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    except Exception:
        return default


def _as_opt_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = _as_str(value, "")
    return s or None


def _as_float(value: Any, default: float = 0.5) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    # clamp to a sane [0, 1] confidence range
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f


def _as_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for item in value:
            s = _as_str(item, "")
            if s:
                out.append(s)
        return out
    return []


def _enum(value: Any, allowed: tuple, default: str) -> str:
    s = _as_str(value, default).strip()
    return s if s in allowed else default


def _as_dict(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def _ensure_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

@dataclass
class EvidenceItem:
    source: str = ""
    command: Optional[str] = None
    service: Optional[str] = None
    modality: str = "execution"
    finding: str = ""
    raw_excerpt: str = ""
    confidence: float = 0.5

    @classmethod
    def from_dict(cls, data: Any) -> "EvidenceItem":
        d = _ensure_dict(data)
        return cls(
            source=_as_str(d.get("source")),
            command=_as_opt_str(d.get("command")),
            service=_as_opt_str(d.get("service")),
            modality=_enum(d.get("modality"), MODALITIES, "execution"),
            finding=_as_str(d.get("finding")),
            raw_excerpt=_as_str(d.get("raw_excerpt"))[:2000],
            confidence=_as_float(d.get("confidence")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Hypothesis
# ---------------------------------------------------------------------------

@dataclass
class Hypothesis:
    service: str = ""
    failure_category: str = ""
    mechanism: str = ""
    confidence: float = 0.0
    supporting_evidence: List[str] = field(default_factory=list)
    contradicting_evidence: List[str] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    repairability: str = "unknown"

    @classmethod
    def from_dict(cls, data: Any) -> "Hypothesis":
        d = _ensure_dict(data)
        return cls(
            service=_as_str(d.get("service")),
            failure_category=_as_str(d.get("failure_category")),
            mechanism=_as_str(d.get("mechanism")),
            confidence=_as_float(d.get("confidence"), 0.0),
            supporting_evidence=_as_str_list(d.get("supporting_evidence")),
            contradicting_evidence=_as_str_list(d.get("contradicting_evidence")),
            missing_evidence=_as_str_list(d.get("missing_evidence")),
            repairability=_enum(d.get("repairability"), REPAIRABILITY, "unknown"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# RCA plan
# ---------------------------------------------------------------------------

@dataclass
class RCAPlan:
    incident_summary: str = ""
    candidate_hypotheses: List[Hypothesis] = field(default_factory=list)
    diagnostic_steps: List[Dict[str, Any]] = field(default_factory=list)
    stop_condition: str = ""

    @classmethod
    def from_dict(cls, data: Any) -> "RCAPlan":
        d = _ensure_dict(data)
        steps = d.get("diagnostic_steps") or []
        norm_steps = [s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []
        return cls(
            incident_summary=_as_str(d.get("incident_summary")),
            candidate_hypotheses=[Hypothesis.from_dict(h) for h in (d.get("candidate_hypotheses") or [])],
            diagnostic_steps=norm_steps,
            stop_condition=_as_str(d.get("stop_condition")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "incident_summary": self.incident_summary,
            "candidate_hypotheses": [h.to_dict() for h in self.candidate_hypotheses],
            "diagnostic_steps": self.diagnostic_steps,
            "stop_condition": self.stop_condition,
        }


# ---------------------------------------------------------------------------
# RCA report
# ---------------------------------------------------------------------------

@dataclass
class RCAReport:
    root_cause_service: str = ""
    failure_category: str = ""
    mechanism: str = ""
    confidence: float = 0.0
    evidence: List[EvidenceItem] = field(default_factory=list)
    uncertainty: str = ""
    repair_hints: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any) -> "RCAReport":
        d = _ensure_dict(data)
        return cls(
            root_cause_service=_as_str(d.get("root_cause_service")),
            failure_category=_as_str(d.get("failure_category")),
            mechanism=_as_str(d.get("mechanism")),
            confidence=_as_float(d.get("confidence"), 0.0),
            evidence=[EvidenceItem.from_dict(e) for e in (d.get("evidence") or [])],
            uncertainty=_as_str(d.get("uncertainty")),
            repair_hints=_as_str_list(d.get("repair_hints")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root_cause_service": self.root_cause_service,
            "failure_category": self.failure_category,
            "mechanism": self.mechanism,
            "confidence": self.confidence,
            "evidence": [e.to_dict() for e in self.evidence],
            "uncertainty": self.uncertainty,
            "repair_hints": self.repair_hints,
        }


# ---------------------------------------------------------------------------
# Repair action / plan
# ---------------------------------------------------------------------------

@dataclass
class RepairAction:
    action_type: str = "WaitAndVerify"
    namespace: str = ""
    target_kind: str = ""
    target_name: str = ""
    target_container: str = ""
    patch: Optional[Dict[str, Any]] = None
    command: Optional[str] = None
    rationale: str = ""
    expected_effect: str = ""
    rollback_hint: str = ""
    evidence_refs: List[str] = field(default_factory=list)
    evidence_basis: str = "none"
    preconditions: List[str] = field(default_factory=list)
    precondition_predicate: Dict[str, Any] = field(default_factory=dict)
    rollback_policy: str = "none"
    risk: str = "low"

    @classmethod
    def from_dict(cls, data: Any) -> "RepairAction":
        d = _ensure_dict(data)
        return cls(
            action_type=_enum(d.get("action_type"), ACTION_TYPES, "WaitAndVerify"),
            namespace=_as_str(d.get("namespace")),
            target_kind=_as_str(d.get("target_kind")),
            target_name=_as_str(d.get("target_name")),
            target_container=_as_str(d.get("target_container")),
            patch=_as_dict(d.get("patch")),
            command=_as_opt_str(d.get("command")),
            rationale=_as_str(d.get("rationale")),
            expected_effect=_as_str(d.get("expected_effect")),
            rollback_hint=_as_str(d.get("rollback_hint")),
            evidence_refs=_as_str_list(d.get("evidence_refs")),
            evidence_basis=_enum(d.get("evidence_basis"), EVIDENCE_BASES, "none"),
            preconditions=_as_str_list(d.get("preconditions")),
            precondition_predicate=_as_dict(d.get("precondition_predicate")) or {},
            rollback_policy=_enum(
                d.get("rollback_policy"), ROLLBACK_POLICIES, "none"
            ),
            risk=_enum(d.get("risk"), RISK_LEVELS, "low"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RepairPlan:
    rca_report: RCAReport = field(default_factory=RCAReport)
    actions: List[RepairAction] = field(default_factory=list)
    verification_plan: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any) -> "RepairPlan":
        d = _ensure_dict(data)
        return cls(
            rca_report=RCAReport.from_dict(d.get("rca_report")),
            actions=[RepairAction.from_dict(a) for a in (d.get("actions") or [])],
            verification_plan=_as_str_list(d.get("verification_plan")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rca_report": self.rca_report.to_dict(),
            "actions": [a.to_dict() for a in self.actions],
            "verification_plan": self.verification_plan,
        }


# ---------------------------------------------------------------------------
# Generic serialization helpers
# ---------------------------------------------------------------------------

def to_dict(obj: Any) -> Any:
    """Recursively convert a schema object (or list/dict thereof) to plain data."""
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return obj.to_dict()
    if isinstance(obj, (list, tuple)):
        return [to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj


def to_json(obj: Any, indent: Optional[int] = None) -> str:
    return json.dumps(to_dict(obj), ensure_ascii=False, indent=indent, default=str)
