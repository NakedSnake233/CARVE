"""Exercise the real runner with mocked cluster/LLM dependencies."""
import json
import sys
from types import SimpleNamespace

from rca_bench.interface import MultimodalContext, RCAResult, RootCauseCandidate
from scripts import run_fulllifecycle


def test_runner_preserves_full_rank_input_and_separate_stage_status(tmp_path, monkeypatch):
    (tmp_path / "experiments").mkdir()
    (tmp_path / "experiments/easy.txt").write_text("cpu-stress\n")
    output = tmp_path / "run.json"
    monkeypatch.chdir(tmp_path)
    context = MultimodalContext("ns", "app", ["a", "b", "c", "d"],
                                raw={"deploy_json": {"items": []}})
    adapter = SimpleNamespace(diagnose=lambda _: RCAResult(
        ranking=[RootCauseCandidate(s, 4 - i) for i, s in enumerate(context.services)],
        predicted_category="cpu-stress"))
    remediator = SimpleNamespace(remediate=lambda **_: {
        "action_type": "RemoveInjectedStress", "executed": False, "safety_ok": True,
        "timings_seconds": {"execution": 0.02}, "planner_route": "deterministic"})
    monkeypatch.setattr("rca_bench.adapters.get_rca", lambda _: adapter)
    monkeypatch.setattr("rca_bench.remediator.get_remediator", lambda **_: remediator)
    monkeypatch.setattr("rca_bench.context_provider.LiveClusterContextProvider",
                        lambda **_: SimpleNamespace(collect=lambda *_: context))
    monkeypatch.setitem(sys.modules, "config", SimpleNamespace(set_config=lambda _: None))
    monkeypatch.setitem(sys.modules, "envs.env", SimpleNamespace(get_random_service=lambda *_: "a"))
    monkeypatch.setitem(sys.modules, "chaos", SimpleNamespace(
        check_status=SimpleNamespace(check=lambda *_: True)))
    monkeypatch.setitem(sys.modules, "chaos.injection", SimpleNamespace(
        inject_failure=lambda *_: True, stop_injection=lambda *_: None))
    monkeypatch.setitem(sys.modules, "chaos.deployment", SimpleNamespace(
        restore_by_original_manifest=lambda *_: None))
    monkeypatch.setattr(run_fulllifecycle.time, "sleep", lambda _: None)
    monkeypatch.setattr(sys, "argv", ["run_fulllifecycle", "--microremed-root", str(tmp_path),
                                    "--difficulties", "easy", "--namespace", "ns", "--env", "app",
                                    "--jaeger-url", "", "--out", str(output),
                                    "--report-path", str(tmp_path / "unused-report.md")])
    # main adjusts sys.path; restore it after the test as well as the cwd.
    original_path = list(sys.path)
    try:
        assert run_fulllifecycle.main() == 0
    finally:
        sys.path[:] = original_path
    record = json.loads(output.read_text())["incidents"][0]
    assert record["predicted_ranking"] == ["a", "b", "c", "d"]
    assert "target" not in record["context"]
    assert record["context"]["raw"] == {"deploy_json": {"items": []}}
    assert record["recovery_diagnostics"]["executed"] is False
    assert record["remediated"] is True  # oracle status is NOT execution status
    assert record["rca_diagnostics"]["attempts"] == []
    assert set(record["timings_seconds"]) >= {
        "injection", "settle", "collection", "rca", "recovery_total", "verification",
        "verification_wait", "stop_injection", "restore", "cleanup_wait"}
