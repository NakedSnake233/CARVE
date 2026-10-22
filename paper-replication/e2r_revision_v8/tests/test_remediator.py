"""Report-driven remediator: builds the fix from the RCA prediction ONLY,
with no chaos selector / no cluster probing (offline; no Ansible execution)."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from rca_bench.remediator import ReportDrivenRemediator  # noqa: E402
from rca_bench.report_update import render_results, START, END, update_report_file  # noqa: E402
from rca_bench.metrics import IncidentEval  # noqa: E402

NS = "online-boutique"
R = ReportDrivenRemediator(use_llm=False)


def test_chaos_repair_uses_category_name_not_selector():
    # cpu-stress on the predicted service -> delete the chaos by name==category;
    # the command must NOT contain a selector or the service name.
    b = R.build(namespace=NS, env=NS, predicted_service="cartservice",
                predicted_category="cpu-stress")
    assert b["action_type"] == "RemoveInjectedStress"
    assert "delete stresschaos cpu-stress" in b["command"]
    assert "--selector" not in b["command"] and "app=" not in b["command"]
    assert "cartservice" not in b["command"]          # service identity not used
    assert b["safety_ok"]


def test_network_subtype_matters():
    bd = R.build(namespace=NS, env=NS, predicted_service="x", predicted_category="network-delay")
    bl = R.build(namespace=NS, env=NS, predicted_service="x", predicted_category="network-loss")
    assert "delete networkchaos network-delay" in bd["command"]
    assert "delete networkchaos network-loss" in bl["command"]   # wrong subtype -> wrong delete


def test_config_error_targets_predicted_service():
    b = R.build(namespace=NS, env=NS, predicted_service="emailservice",
                predicted_category="pod-config-error")
    assert b["action_type"] == "RecoverConfigError"
    assert "set resources deployment/emailservice" in b["command"]   # predicted service matters


def test_unknown_category_is_waitandverify():
    b = R.build(namespace=NS, env=NS, predicted_service="x", predicted_category="service")
    assert b["action_type"] == "WaitAndVerify"      # cannot fix -> will fail verification


def test_disk_io_maps_to_iochaos_delete():
    b = R.build(namespace=NS, env=NS, predicted_service="x", predicted_category="disk-io")
    assert "delete iochaos disk-io" in b["command"]


def test_pod_fail_maps_to_podchaos_delete():
    b = R.build(namespace=NS, env=NS, predicted_service="x", predicted_category="pod-fail")
    assert "delete podchaos pod-fail" in b["command"]


def test_report_update_renders_and_splices():
    rca_by_diff = {"easy": [
        IncidentEval("1", "cpu-stress", "A", ["A", "B"], "cpu-stress"),
        IncidentEval("2", "network-loss", "B", ["A", "C"], "cpu-stress"),
    ]}
    rem_by_diff = {"easy": [("cpu-stress", True), ("network-loss", False)]}
    section = render_results(rca_by_diff, rem_by_diff, n_done=2, n_total=10,
                             rca_method="planrca", settle=40, hide_artifacts=True)
    assert "AC@1" in section and "AC@3" in section and "AC@5" in section and "MRR" in section
    assert "修复成功率" in section
    # splice into a tiny doc with markers
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
        fh.write(f"# T\n{START}\nOLD\n{END}\ntail\n")
        path = fh.name
    assert update_report_file(path, section)
    txt = open(path).read()
    assert "OLD" not in txt and "AC@1" in txt and "tail" in txt
    os.unlink(path)


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} remediator/report tests passed.")


if __name__ == "__main__":
    _run_all()
