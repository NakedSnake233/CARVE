"""Schema serialization + robust-parsing tests (no cluster, no LLM)."""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from methods.PlanRCAThinkRemed import schemas  # noqa: E402
from methods.PlanRCAThinkRemed.schemas import (  # noqa: E402
    EvidenceItem, Hypothesis, RCAPlan, RCAReport, RepairAction, RepairPlan, to_json,
)


def test_evidence_roundtrip():
    e = EvidenceItem(source="chaos", command="kubectl get x", service="cartservice",
                     modality="event", finding="active StressChaos", raw_excerpt="{}",
                     confidence=0.9)
    d = e.to_dict()
    assert json.loads(json.dumps(d))  # serializable
    e2 = EvidenceItem.from_dict(d)
    assert e2.service == "cartservice"
    assert e2.modality == "event"
    assert abs(e2.confidence - 0.9) < 1e-9


def test_partial_and_messy_dicts_do_not_crash():
    # missing fields, wrong types, junk values
    h = Hypothesis.from_dict({"service": "svc", "confidence": "not-a-number",
                              "supporting_evidence": "single", "repairability": "bogus"})
    assert h.service == "svc"
    assert h.confidence == 0.0          # bad float -> default
    assert h.supporting_evidence == ["single"]
    assert h.repairability == "unknown"  # invalid enum -> default

    # confidence clamping
    assert EvidenceItem.from_dict({"confidence": 5.0}).confidence == 1.0
    assert EvidenceItem.from_dict({"confidence": -2}).confidence == 0.0

    # modality coercion
    assert EvidenceItem.from_dict({"modality": "weird"}).modality == "execution"


def test_repairplan_nested_roundtrip():
    plan = RepairPlan(
        rca_report=RCAReport(root_cause_service="cartservice", failure_category="cpu-stress",
                             confidence=0.95, evidence=[EvidenceItem(finding="x")]),
        actions=[RepairAction(action_type="RemoveInjectedStress", namespace="online-boutique",
                              target_kind="StressChaos", target_name="cpu-stress",
                              command="kubectl delete stresschaos cpu-stress -n online-boutique")],
        verification_plan=["kubectl get pods -n online-boutique"],
    )
    blob = to_json(plan)
    parsed = json.loads(blob)
    assert parsed["rca_report"]["root_cause_service"] == "cartservice"
    assert parsed["actions"][0]["action_type"] == "RemoveInjectedStress"

    plan2 = RepairPlan.from_dict(parsed)
    assert plan2.rca_report.failure_category == "cpu-stress"
    assert plan2.actions[0].target_name == "cpu-stress"
    assert plan2.verification_plan == ["kubectl get pods -n online-boutique"]


def test_unknown_action_type_defaults_to_waitandverify():
    a = RepairAction.from_dict({"action_type": "DeleteEverything", "target_name": "x"})
    assert a.action_type == "WaitAndVerify"
    assert a.risk == "low"


def test_rcaplan_from_dict_filters_non_dict_steps():
    plan = RCAPlan.from_dict({
        "incident_summary": "s",
        "candidate_hypotheses": [{"service": "a", "confidence": 0.5}],
        "diagnostic_steps": [{"target_modality": "resource"}, "garbage", 42],
        "stop_condition": "done",
    })
    assert len(plan.candidate_hypotheses) == 1
    assert plan.diagnostic_steps == [{"target_modality": "resource"}]


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} schema tests passed.")


if __name__ == "__main__":
    _run_all()
