"""Configuration projection regressions; no native resources are used."""
import copy
from dataclasses import replace

import pytest

from methods.E2R.intervention import action_context as context
from methods.E2R.intervention.contracts import Policy, compile_mutation
from methods.E2R.intervention.schema import Action, Entity, EvidenceGraph, Fact, Plan, Relation


def template(name, host="shared-db"):
    return {"metadata": {"labels": {"app": name, "track": "stable"}},
            "spec": {"containers": [{"name": "app", "image": "registered:v1",
                     "env": [{"name": "STATION_MYSQL_HOST", "value": host}]}]}}


def resource(kind, name, spec):
    return {"kind": kind, "metadata": {"name": name, "namespace": "test",
            "uid": kind + ":" + name, "resourceVersion": "1"}, "spec": spec,
            **({"status": {"readyReplicas": 1}} if kind == "Deployment" else {})}


class Client:
    def __init__(self, objects):
        self.objects = objects
        self.calls = []

    def get(self, kind, name):
        self.calls.append((kind, name))
        return copy.deepcopy(self.objects[kind, name])


@pytest.fixture
def scene(monkeypatch):
    monkeypatch.setattr(context.time, "time", lambda: 100.)
    objects = {("Service", "entry"): resource("Service", "entry", {"selector": {"app": "new"}})}
    entities = [Entity("entry", "service", "Service", "test", "entry", "Service:entry", "1")]
    for name, role, identity in (("new", "deployment", "new"), ("old", "deployment", "old"),
                                  ("shared-db", "shared_dependency", "db"),
                                  ("private-db", "shared_dependency", "private")):
        objects["Deployment", name] = resource("Deployment", name, {
            "replicas": 1, "selector": {"matchLabels": {"app": name}}, "template": template(name)})
        entities.append(Entity(identity, role, "Deployment", "test", name, "Deployment:" + name, "1"))
        if role == "shared_dependency":
            objects["Service", name] = resource("Service", name, {"selector": {"app": name}})
    entities += [Entity("approved", "deployment"), Entity("state", "goal")]
    facts = [Fact(e.id + ":" + key, e.id, key, value, 90., 300., "measured", uid=e.uid)
             for e in entities if e.id in ("new", "old")
             for key, value in (("healthy", True), ("capacity", 10.), ("load", 2.))]
    facts += [Fact("error", "entry", "error_rate", .5, 90., 300., "transaction", uid="Service:entry"),
              Fact("latency", "entry", "latency_ms", 250., 90., 300., "transaction", uid="Service:entry")]
    relations = [Relation("entry", key, "depends_on", 90., 1000., "configuration")
                 for key in ("new", "old", "state", "approved")]
    relations += [Relation("state", "db", "depends_on", 90., 1000., "configuration")]
    graph = EvidenceGraph(100., entities, facts, relations, ["entry", "state"])
    policy = Policy("test", ["entry", "new", "old"],
                    selector_options={"entry": {"standby": {"destination": "old", "selector": {"app": "old"}}}},
                    rollback_options={"new": {"good": {"destination": "approved", "template": template("new")}}},
                    max_replicas={"new": 3})
    plans = {"rollback": Plan("rollback", (Action("rollback_version", "new", {"option": "good"}, "approved"),)),
             "switch": Plan("switch", (Action("restore_selector", "entry", {"option": "standby"}, "old"),)),
             "none": Plan("none", ())}
    client = Client(objects)
    baseline = context.capture_configuration(client, graph)
    reference = {"healthy": True, "capacity": 10., "observed_at": 100., "baseline_context": baseline}
    return client, graph, policy, plans, reference


def project(scene, arm="rollback", now=100.):
    client, graph, policy, plans, reference = scene
    snapshot = context.capture_configuration(client, graph, reference)
    return context.project_action_context(graph, plans[arm], policy, snapshot, now=now)


def test_capture_is_actual_get_receipts_and_preserves_reference(scene):
    client, graph, _, _, reference = scene
    snapshot = context.capture_configuration(client, graph, reference)
    assert set(snapshot["objects"]) == {"entry", "new", "old", "db", "private"}
    assert set(snapshot["store_services"]) == {"db", "private"}
    assert ("Service", "shared-db") in client.calls
    for group in ("objects", "store_services"):
        for receipt in snapshot[group].values():
            assert receipt["observed_at"] == 100.
            assert receipt["uid"] == receipt["object"]["metadata"]["uid"]
            assert receipt["resource_version"] == "1"
    reference["baseline_context"]["objects"]["new"]["object"]["spec"]["replicas"] = 99
    assert snapshot["reference"]["baseline_context"]["objects"]["new"]["object"]["spec"]["replicas"] == 1


def test_bad_template_is_repairable_using_approved_template_not_old_health(scene):
    client, graph, policy, plans, _ = scene
    obj = client.objects["Deployment", "new"]
    obj["spec"]["template"]["spec"]["containers"][0]["image"] = "broken:v2"
    obj["status"]["readyReplicas"] = 0
    graph.facts = [replace(f, value=False) if f.entity == "new" and f.variable == "healthy" else f for f in graph.facts]
    before = copy.deepcopy(obj)
    witness = project(scene)
    assert witness["status"] == "eligible"
    assert witness["current_status"] == "unknown"
    assert witness["reference_status"]["status"] == "applicable"
    assert witness["features"]["current_template_matches_approved"] == 0
    assert witness["features"]["projected_template_matches_approved"] == 1
    assert witness["features"]["target_ready_replicas"] == 0
    assert witness["features"]["target_healthy"] == 0
    assert obj == before  # Projection is a pure read, using the writer's semantics.
    assert compile_mutation(plans["rollback"].actions[0], graph, policy, obj).after == template("new")


def test_rollback_does_not_repair_an_unrelated_bad_service_selector(scene):
    client, *_ = scene
    client.objects["Service", "entry"]["spec"]["selector"] = {"app": "absent"}
    witness = project(scene)
    assert witness["status"] == "blocked"
    assert "business_selector_matches_no_deployment" in witness["reasons"]
    assert witness["features"]["projected_route_matches"] == 0


def test_full_rollback_template_can_restore_extra_labels_used_by_service(scene):
    client, *_ = scene
    client.objects["Service", "entry"]["spec"]["selector"] = {"app": "new", "track": "stable"}
    client.objects["Deployment", "new"]["spec"]["template"]["metadata"]["labels"]["track"] = "bad"
    witness = project(scene)
    assert witness["status"] == "eligible"
    assert witness["features"]["current_route_matches"] == 0
    assert witness["features"]["projected_route_matches"] == 1


def test_zero_desired_app_replicas_is_structural_obstacle(scene):
    client, *_ = scene
    client.objects["Deployment", "new"]["spec"]["replicas"] = 0
    witness = project(scene)
    assert witness["status"] == "blocked"
    assert "app_desired_replicas_zero:new" in witness["reasons"]
    assert witness["features"]["projected_goal_owner_match"] == 1
    assert witness["features"]["projected_required_store_replicas"] == 1


def test_rollback_can_change_db_binding_away_from_stopped_irrelevant_store(scene):
    client, *_ = scene
    client.objects["Deployment", "new"]["spec"]["template"]["spec"]["containers"][0]["env"][0]["value"] = "private-db"
    client.objects["Deployment", "private-db"]["spec"]["replicas"] = 0
    witness = project(scene)
    assert witness["status"] == "eligible"
    assert witness["current_status"] == "blocked"
    assert witness["features"]["db_binding_changed"] == 1
    assert witness["features"]["projected_goal_owner_match"] == 1
    assert witness["reference_status"]["status"] == "applicable"
    client.objects["Deployment", "shared-db"]["spec"]["replicas"] = 0
    witness = project(scene)
    assert witness["status"] == "blocked"
    assert "store_desired_replicas_zero:db" in witness["reasons"]


@pytest.mark.parametrize("target", ["new", "shared-db"])
def test_ready_zero_and_transient_health_failure_are_unknown_not_blocked(scene, target):
    client, graph, *_ = scene
    client.objects["Deployment", target]["status"]["readyReplicas"] = 0
    witness = project(scene)
    assert witness["status"] == "unknown"
    assert witness["projected_status"] == "unknown"
    client.objects["Deployment", target]["status"]["readyReplicas"] = 1
    if target == "new":
        graph.facts = [replace(f, value=False) if f.entity == "new" and f.variable == "healthy" else f for f in graph.facts]
    else:
        graph.facts.append(Fact("db-health", "db", "healthy", False, 99., 100., "probe", uid="Deployment:shared-db"))
    assert project(scene)["status"] == "unknown"


@pytest.mark.parametrize("change", ["missing", "expired", "uid", "store_service", "rv", "future_time"])
def test_missing_expired_or_identity_changed_snapshot_is_unknown(scene, change):
    client, graph, policy, plans, reference = scene
    snapshot = context.capture_configuration(client, graph, reference)
    now = 100.
    if change == "missing":
        del snapshot["objects"]["new"]
    elif change == "expired":
        now = 131.
    elif change == "uid":
        snapshot["objects"]["new"]["object"]["metadata"]["uid"] = "replacement"
        snapshot["objects"]["new"]["uid"] = "replacement"
    elif change == "rv":
        snapshot["objects"]["new"]["resource_version"] = "different"
    elif change == "future_time":
        snapshot["captured_at"] = 101.
    else:
        del snapshot["store_services"]["db"]
    witness = context.project_action_context(graph, plans["rollback"], policy, snapshot, now=now)
    assert witness["status"] == "unknown"
    assert witness["required_observations"]


def test_historical_health_cannot_override_changed_necessary_dependency(scene):
    client, *_ = scene
    db = client.objects["Deployment", "shared-db"]
    db["spec"]["template"]["spec"]["containers"][0]["resources"] = {"limits": {"cpu": ".01"}}
    witness = project(scene)
    assert witness["status"] == "unknown"
    assert witness["reference_status"]["status"] == "inapplicable"
    assert "reference_dependency_configuration_changed:db" in witness["reference_status"]["reasons"]


def test_historical_context_uses_reference_lifetime_and_current_load(scene, monkeypatch):
    client, graph, *_ = scene
    monkeypatch.setattr(context.time, "time", lambda: 200.)
    graph.at = 200.
    assert project(scene, now=200.)["reference_status"]["status"] == "applicable"
    graph.facts = [replace(f, value=3.) if f.entity == "new" and f.variable == "load" else f for f in graph.facts]
    witness = project(scene, now=200.)
    assert witness["status"] == "unknown"
    assert "reference_load_or_capacity_not_applicable" in witness["reference_status"]["reasons"]


def test_approved_template_must_have_actually_been_measured(scene):
    _, _, _, _, reference = scene
    reference["baseline_context"]["objects"]["new"]["object"]["spec"]["template"]["spec"]["containers"][0]["image"] = "some-other-version"
    witness = project(scene)
    assert witness["status"] == "unknown"
    assert "reference_did_not_measure_approved_template" in witness["reference_status"]["reasons"]


def test_prewrite_fresh_snapshot_refuses_new_change_despite_same_object_uids(scene):
    client, *_ = scene
    before = project(scene)
    assert before["status"] == "eligible"
    client.objects["Deployment", "shared-db"]["spec"]["replicas"] = 0
    client.objects["Deployment", "shared-db"]["metadata"]["resourceVersion"] = "2"
    after = project(scene)
    assert after["status"] == "blocked"
    assert after["features"]["projected_required_store_replicas"] == 0


def test_one_blocked_route_does_not_hide_another_valid_business_path(scene):
    client, graph, policy, plans, reference = scene
    client.objects["Deployment", "new"]["spec"]["template"]["spec"]["containers"][0]["env"][0]["value"] = "private-db"
    # Both real templates share track=stable; a mixed selector has one path
    # preserving the required state and another path that cannot preserve it.
    policy.selector_options["entry"]["standby"]["selector"] = {"track": "stable"}
    witness = project(scene, "switch")
    assert witness["status"] == "eligible"
    assert {p["status"] for p in witness["paths"]} == {"eligible", "blocked"}


def test_actual_store_service_route_determines_owner_not_hostname_alone(scene):
    client, *_ = scene
    client.objects["Deployment", "old"]["spec"]["template"]["spec"]["containers"][0]["env"][0]["value"] = "private-db"
    client.objects["Service", "private-db"]["spec"]["selector"] = {"app": "shared-db"}
    witness = project(scene, "switch")
    assert witness["status"] == "eligible"
    assert witness["paths"][0]["store"] == "db"


def test_database_host_in_another_namespace_is_not_the_local_owner(scene):
    client, *_ = scene
    client.objects["Deployment", "old"]["spec"]["template"]["spec"]["containers"][0]["env"][0]["value"] = "shared-db.other.svc.cluster.local"
    witness = project(scene, "switch")
    assert witness["status"] == "unknown"
    assert "store_binding_or_service_unknown:old" in witness["reasons"]


def test_no_action_safe_fallback_retains_observed_failure_and_feature_order(scene):
    client, *_ = scene
    client.objects["Deployment", "shared-db"]["spec"]["replicas"] = 0
    witness = project(scene, "none")
    assert witness["status"] == "eligible"
    assert witness["current_status"] == "blocked"
    assert witness["projected_status"] == "blocked"
    assert witness["features"]["current_required_store_replicas"] == 0
    assert witness["features"]["entry_error_rate"] == .5
    assert tuple(witness["features"]) == context.FEATURE_NAMES


def test_unobserved_probe_features_are_missing_and_request_measurement(scene):
    _, graph, *_ = scene
    graph.facts = [f for f in graph.facts if f.entity != "old" or f.variable not in ("healthy", "capacity")]
    witness = project(scene, "switch")
    assert witness["features"]["target_healthy"] is None
    assert witness["features"]["target_capacity"] is None
    assert {"old:healthy", "old:capacity"} <= set(witness["required_observations"])


def test_capture_read_failure_and_no_baseline_reference_never_invent_health(scene):
    client, graph, policy, plans, _ = scene
    snapshot = context.capture_configuration(client, graph)
    witness = context.project_action_context(graph, plans["rollback"], policy, snapshot, now=100.)
    assert witness["status"] == "unknown"
    assert "baseline_context_missing" in witness["reference_status"]["reasons"]
    del client.objects["Service", "shared-db"]
    snapshot = context.capture_configuration(client, graph)
    assert snapshot["store_services"]["db"]["error"].startswith("KeyError")
    assert context.project_action_context(graph, plans["switch"], policy, snapshot, now=100.)["status"] == "unknown"
