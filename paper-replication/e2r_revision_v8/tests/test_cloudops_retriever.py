"""Cloud-OpsBench retriever tests, incl. the oracle-hidden prompt check."""

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from methods.PlanRCAThinkRemed import cloudops_retriever, prompts  # noqa: E402


def _make_fixture(root):
    case = os.path.join(root, "benchmark", "boutique", "cpu_stress", "case-001")
    os.makedirs(os.path.join(case, "raw_data"))
    with open(os.path.join(case, "metadata.json"), "w") as fh:
        json.dump({"service": "cartservice", "diagnosis": "cpu saturation on cartservice",
                   "remediation": "remove cpu stress"}, fh)
    with open(os.path.join(case, "tool_cache.json"), "w") as fh:
        json.dump({"tools": [{"name": "kubectl_top"}, {"name": "get_pods"}]}, fh)
    with open(os.path.join(case, "raw_data", "alert.json"), "w") as fh:
        json.dump({"alert": "High CPU usage on cartservice pod"}, fh)
    gold = os.path.join(root, "golden-trajectory", "boutique", "cpu_stress", "case-001")
    os.makedirs(gold)
    with open(os.path.join(gold, "path1.json"), "w") as fh:
        json.dump({"diagnosis": "cpu stress", "action": "delete stresschaos"}, fh)


def test_missing_root_returns_empty():
    assert cloudops_retriever.load_cloudops_examples("/no/such/path") == []
    assert cloudops_retriever.retrieve_examples("cpu", "online-boutique", 3,
                                                cloudopsbench_root="/no/such/path") == []


def test_parses_tiny_fixture():
    with tempfile.TemporaryDirectory() as tmp:
        _make_fixture(tmp)
        examples = cloudops_retriever.load_cloudops_examples(tmp)
        assert len(examples) == 1
        ex = examples[0]
        assert ex["system"] == "boutique"
        assert ex["fault_category"] == "cpu_stress"
        assert "cartservice" in ex["services"]
        assert any("top" in t for t in ex["useful_tools"])


def test_retrieve_maps_env_and_ranks():
    with tempfile.TemporaryDirectory() as tmp:
        _make_fixture(tmp)
        hits = cloudops_retriever.retrieve_examples(
            "high cpu usage on cartservice", "online-boutique", 3, cloudopsbench_root=tmp,
        )
        assert len(hits) == 1
        # internal search blob must be stripped before returning to prompts
        assert all(not k.startswith("_") for k in hits[0].keys())


def test_simple_micro_returns_empty():
    with tempfile.TemporaryDirectory() as tmp:
        _make_fixture(tmp)
        assert cloudops_retriever.retrieve_examples("cpu", "simple-micro", 3,
                                                    cloudopsbench_root=tmp) == []


def test_oracle_labels_hidden_in_rca_prompts_by_default():
    # Default (use_oracle_rca=False -> oracle_hint=None): labels must NOT appear.
    msgs = prompts.build_rca_planner_messages(
        runtime_envs="k3s", namespace="online-boutique", env="online-boutique",
        incident_context="An alert fired.", examples=None, oracle_hint=None,
    )
    text = " ".join(m["content"] for m in msgs)
    assert "cartservice" not in text
    assert "cpu-stress" not in text
    assert "ORACLE" not in text.upper()

    # Ablation (oracle_hint provided): labels appear, clearly marked DEBUG.
    msgs2 = prompts.build_rca_planner_messages(
        runtime_envs="k3s", namespace="online-boutique", env="online-boutique",
        incident_context="An alert fired.", examples=None,
        oracle_hint={"root_cause": "cartservice", "failure_category": "cpu-stress"},
    )
    text2 = " ".join(m["content"] for m in msgs2)
    assert "cartservice" in text2 and "cpu-stress" in text2
    assert "ORACLE" in text2.upper()


def test_oracle_labels_hidden_in_repair_prompts_by_default():
    from methods.PlanRCAThinkRemed.schemas import RCAReport
    msgs = prompts.build_repair_planner_messages(
        runtime_envs="k3s", namespace="online-boutique", env="online-boutique",
        rca_report_json=RCAReport().to_dict(), evidence_history_json=[], oracle_hint=None,
    )
    text = " ".join(m["content"] for m in msgs)
    assert "ORACLE" not in text.upper()


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} cloudops/oracle tests passed.")


if __name__ == "__main__":
    _run_all()
