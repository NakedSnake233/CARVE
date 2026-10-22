import copy
import json
from dataclasses import asdict, replace

import pytest

from methods.E2R.intervention.campaign import run_campaign
from methods.E2R.intervention.contracts import Policy, field_value
from methods.E2R.intervention.examples import example_bundle
from methods.E2R.intervention.platform import Executor, LiveProvider, Probe
from methods.E2R.intervention.runtime import ObservationBudget, refine_and_decide, rank_candidates
from methods.E2R.intervention.schema import EFFECTS, Fact, Plan, input_bundle


class FakeClient:
    namespace = "e2r-test"

    def __init__(self):
        self.writes = 0
        self.objects = {}
        for name, kind, spec in (("front", "Service", {"selector": {"app": "new"}}),
                                 ("old", "Deployment", {"replicas": 1}),
                                 ("new", "Deployment", {"replicas": 1, "selector": {"matchLabels": {"app": "new"}},
                                  "template": {"metadata": {"labels": {"app": "new"}},
                                               "spec": {"containers": [{"name": "app", "image": "example:v2"}]}}})):
            self.objects[(kind, name)] = {"kind": kind, "metadata": {"uid": "uid-"+name, "name": name,
                    "namespace": self.namespace, "resourceVersion": "1", "generation": 1}, "spec": spec}

    def get(self, kind, name):
        value = copy.deepcopy(self.objects[(kind, name)])
        if kind == "Deployment":
            n = value["spec"]["replicas"]
            value["status"] = {"observedGeneration": 1, "updatedReplicas": n, "readyReplicas": n, "availableReplicas": n}
        return value

    def patch(self, kind, name, patch, *, dry_run=False):
        value = self.get(kind, name)
        for op in patch:
            if op["op"] == "test":
                if field_value(value, op["path"]) != op["value"]:
                    raise RuntimeError("conflict")
            else:
                node = value
                pieces = op["path"].strip("/").split("/")
                for piece in pieces[:-1]:
                    node = node[piece]
                node[pieces[-1]] = copy.deepcopy(op["value"])
        value["metadata"]["resourceVersion"] = str(int(value["metadata"]["resourceVersion"])+1)
        if not dry_run:
            self.writes += 1
            self.objects[(kind, name)] = copy.deepcopy(value)
        return value


class FakeProvider:
    def ready_endpoints(self, name, selector):
        return 1


def test_all_three_real_adapter_paths_dry_run_execute_and_compensate(tmp_path):
    bundle, policy = example_bundle()
    graph, plans = input_bundle(bundle)
    client = FakeClient()
    executor = Executor(client, FakeProvider(), Policy.from_dict(policy), tmp_path/"journal.jsonl")
    for plan in (plans[1], Plan("scale_only", (plans[2].actions[0],)), plans[3]):
        result = executor.execute(graph, plan, dry_run=True)
        assert result["status"] == "dry_run" and not result["executed"]
    assert client.writes == 0
    result = executor.execute(graph, plans[1], dry_run=False, refresh=lambda: graph)
    assert result["status"] == "executed"
    assert "business_recovered" not in result
    assert client.get("Service", "front")["spec"]["selector"] == {"app": "old"}
    assert executor.compensate()[0]["restored"]
    assert client.get("Service", "front")["spec"]["selector"] == {"app": "new"}


def test_recreated_target_requires_replanning(tmp_path):
    bundle, policy = example_bundle()
    graph, plans = input_bundle(bundle)
    refreshed = copy.deepcopy(graph)
    refreshed.entities = [replace(e, uid="new-resource") if e.id == "front" else e for e in graph.entities]
    client = FakeClient()
    executor = Executor(client, FakeProvider(), Policy.from_dict(policy), tmp_path/"journal.jsonl")
    result = executor.execute(graph, plans[1], dry_run=False, refresh=lambda: refreshed)
    assert result["status"] == "rejected" and client.writes == 0


def test_transport_error_after_server_write_still_has_compensation(tmp_path):
    bundle, policy = example_bundle()
    graph, plans = input_bundle(bundle)
    class LostResponse(FakeClient):
        lose = True
        def patch(self, kind, name, patch, *, dry_run=False):
            value = super().patch(kind, name, patch, dry_run=dry_run)
            if self.lose:
                self.lose = False
                raise TimeoutError("response lost after server accepted the write")
            return value
    client = LostResponse()
    executor = Executor(client, FakeProvider(), Policy.from_dict(policy), tmp_path/"journal.jsonl")
    result = executor.execute(graph, plans[1], dry_run=False, refresh=lambda: graph)
    assert result["status"] == "execution_unknown"
    assert executor.compensate()[0]["restored"]
    assert client.get("Service", "front")["spec"]["selector"] == {"app": "new"}


class PredictableEffects:
    source = "live"

    def predict(self, graph, plan):
        probabilities = {k: 0.999 for k in EFFECTS}
        if not plan.actions:
            probabilities["business_recovered"] = 0.1
        return {"probabilities": probabilities, "spread": {k: 0.0 for k in EFFECTS},
                "support": {k: 50 for k in EFFECTS}, "source": self.source,
                "unseen_signature": False, "outside_training_range": []}


def test_unknown_capacity_causes_bounded_read_and_replanning():
    bundle, policy = example_bundle(hide_capacity=True)
    graph, plans = input_bundle(bundle)
    probe = Probe("capacity", "old", "capacity", "kubernetes", [10, 100],
                  resource_kind="Deployment", resource_name="old", json_path=("status", "capacity"))
    def observe(spec, _graph):
        return Fact("measured", "old", "capacity", 100, 101, 30, "probe:capacity", uid="uid-old")
    result = refine_and_decide(graph, plans, Policy.from_dict(policy), PredictableEffects(), [probe], observe,
                               budget=ObservationBudget(max_calls=1), wall_clock=lambda: 101)
    assert result["decision"] == "execute" and result["selected_plan"] in ("switch", "rollback")
    assert result["budget"]["calls"] == 1 and len(result["observations"]) == 1
    assert result["observations"][0]["acquisition"]["prior"] == "uniform_heuristic"
    assert graph.read("old", "capacity") is None  # Inputs are immutable.


def test_synthetic_support_and_over_budget_observations_never_authorize_execution():
    bundle, policy = example_bundle()
    graph, plans = input_bundle(bundle)
    model = PredictableEffects()
    model.source = "synthetic"
    assert not any(r["executable"] for r in rank_candidates(graph, plans, Policy.from_dict(policy), model))
    bundle, policy = example_bundle(hide_capacity=True)
    graph, plans = input_bundle(bundle)
    clock = [0.0]
    probe = Probe("capacity", "old", "capacity", "kubernetes", [100], timeout=1,
                  resource_kind="Deployment", resource_name="old")
    def slow(spec, _graph):
        clock[0] += 3
        return Fact("late", "old", "capacity", 100, 101, 30, "probe", uid="uid-old")
    result = refine_and_decide(graph, plans, Policy.from_dict(policy), PredictableEffects(), [probe], slow,
               budget=ObservationBudget(max_calls=1, max_seconds=2), clock=lambda: clock[0], wall_clock=lambda: 101)
    assert result["decision"] == "abstain" and result["observations"][0]["status"] == "budget_exceeded"


def test_ready_endpoint_with_wrong_version_does_not_pass():
    class Client:
        namespace = "e2r-test"
        def get(self, kind, name):
            if kind == "Service":
                return {"spec": {}}
            return {"metadata": {"uid": "p1", "labels": {"version": "bad"}},
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]}}
        def list(self, kind, selector):
            return [{"endpoints": [{"conditions": {"ready": True},
                                    "targetRef": {"kind": "Pod", "name": "pod", "uid": "p1"}}]}]
    provider = LiveProvider({"namespace": "e2r-test", "entities": [], "goals": []}, Client())
    assert provider.ready_endpoints("front", {"version": "good"}) == 0


def test_campaign_restores_each_arm_and_preserves_rejections(tmp_path):
    bundle, _ = example_bundle()
    class Backend:
        source = "synthetic"
        def __init__(self, config, path):
            self.active = False
        def start(self, case, path):
            assert not self.active
            self.active = True
        def observe(self, case):
            return bundle
        def execute(self, graph, plan, directory):
            return {"status": "no_action" if not plan.actions else "rejected", "plan_id": plan.id}
        def verify(self, graph, plan, execution, at):
            return {"observed_at": at+1, "effects": {k: False for k in EFFECTS}}
        def restore(self):
            self.active = False
            return {"status": "restored"}
    config = {"application": "fixture", "cases": [{"group_id": "g", "condition_id": "a"}],
              "arms": ["no_action", "switch"], "repeats": 2}
    result = run_campaign(config, tmp_path/"campaign", Backend)
    assert result["attempted"] == 4 and result["observed"] == 2 and result["unknown_or_rejected"] == 2
    rows = [json.loads(s) for s in (tmp_path/"campaign/episodes.jsonl").read_text().splitlines()]
    assert all(all(v is None for v in r["outcome"]["effects"].values()) for r in rows if r["status"] == "rejected")
