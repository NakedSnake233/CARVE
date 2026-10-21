"""Observed configuration and pure, single-action necessary-condition witnesses.

This adapter is deliberately limited to the registered Station/Cart inventory
and its single-owner state goal. A viable path is a necessary condition, never
an intervention outcome or a training label.
"""
from __future__ import annotations

import copy
import time

from .contracts import action_option, compile_mutation
from .schema import number


FEATURE_NAMES = (
    "current_route_matches", "projected_route_matches",
    "current_routed_replicas", "projected_routed_replicas",
    "current_routed_ready_replicas", "current_template_matches_approved",
    "projected_template_matches_approved", "current_goal_owner_match",
    "projected_goal_owner_match", "current_required_store_replicas",
    "projected_required_store_replicas", "current_required_store_ready_replicas",
    "projected_required_store_route_matches", "target_desired_replicas",
    "target_ready_replicas", "target_healthy", "target_capacity", "offered_load",
    "entry_error_rate", "entry_latency_ms", "replica_delta", "template_changed",
    "db_binding_changed", "selector_changed", "reference_applicable",
    "reference_capacity", "reference_load",
)


def _fact(graph, entity, variable, now):
    if entity is None:
        return None
    value = graph.latest(entity, variable)
    return value.value if value and value.usable(now, graph.entity(entity).uid) else None


def _owners(graph, now):
    goals = {e.id for e in graph.entities if e.role == "goal" and e.id in graph.goals}
    stores = {e.id for e in graph.entities if e.role == "shared_dependency" and e.kind == "Deployment"}
    obligations = [r for r in graph.known_relations()
                   if r.source in goals and r.target in stores and r.kind == "depends_on"]
    if (not goals or not obligations or any(
            r.validity != "present" or not r.observed_at <= now <= r.observed_at + r.ttl
            for r in obligations)):
        return None
    owners = {r.target for r in obligations}
    return owners if len(owners) == 1 else None


def capture_configuration(client, graph, reference=None, ttl_seconds=30):
    """GET actual objects, preserving individual read timestamps and identities.

    Store Services are not graph entities in the native adapter, so retain
    their GET receipts separately, keyed by the corresponding store entity.
    Failure to read an object stays missing evidence, including a failed GET.
    """
    if not number(ttl_seconds) or ttl_seconds <= 0:
        raise ValueError("configuration lifetime must be positive")

    def get(kind, name):
        stamp = time.time()
        try:
            obj = copy.deepcopy(client.get(kind, name))
            meta = obj.get("metadata", {})
            return {"object": obj, "observed_at": stamp, "uid": meta.get("uid"),
                    "resource_version": meta.get("resourceVersion")}
        except Exception as error:
            return {"object": None, "observed_at": stamp, "uid": None,
                    "resource_version": None, "error": type(error).__name__ + ": " + str(error)}

    objects = {e.id: get(e.kind, e.name) for e in graph.entities
               if e.kind in ("Deployment", "Service")}
    services = {e.id: get("Service", e.name) for e in graph.entities
                if e.role == "shared_dependency" and e.kind == "Deployment"}
    now = time.time()
    return {"schema": "action_configuration_v1", "captured_at": now,
            "ttl_seconds": ttl_seconds, "objects": objects, "store_services": services,
            "load_by_entity": {e.id: _fact(graph, e.id, "load", now) for e in graph.entities},
            "owner_entities": sorted(_owners(graph, now) or []),
            "reference": copy.deepcopy(reference)}


def _receipt(snapshot, group, entity, now, *, historical=False, service=False):
    receipt = snapshot.get(group, {}).get(entity.id, {})
    obj = receipt.get("object")
    label = ("store_service:" if service else "object:") + entity.id
    if not isinstance(obj, dict):
        return None, label + ":missing"
    meta = obj.get("metadata", {})
    if (obj.get("kind") != ("Service" if service else entity.kind)
            or meta.get("name") != entity.name or meta.get("namespace") != entity.namespace
            or not meta.get("uid") or not meta.get("resourceVersion")
            or receipt.get("uid") != meta.get("uid")
            or receipt.get("resource_version") != meta.get("resourceVersion")
            or not service and (not entity.uid or meta.get("uid") != entity.uid)):
        return None, label + ":identity_mismatch"
    stamp, ttl = receipt.get("observed_at"), snapshot.get("ttl_seconds")
    if (not number(stamp) or not number(ttl) or ttl <= 0 or stamp > now
            or not historical and now > stamp + ttl):
        return None, label + ":expired_or_invalid_time"
    return obj, None


def _host(template, namespace):
    """Resolve the literal native adapter bindings; indirect env stays unknown."""
    bindings = []
    for container in template.get("spec", {}).get("containers", []):
        for env in container.get("env", []):
            if env.get("name") in ("REDIS_ADDR", "STATION_MYSQL_HOST", "SPRING_DATASOURCE_URL"):
                value = env.get("value")
                if not isinstance(value, str) or not value or "$(" in value:
                    return None
                address = value.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0].rstrip(".")
                parts = address.split(".")
                if len(parts) > 1 and (parts[1] != namespace or parts[2:] not in ([], ["svc"], ["svc", "cluster", "local"])):
                    return None
                bindings.append(parts[0])
    return bindings[0] if len(set(bindings)) == 1 else None


def _selector(obj):
    selector = obj.get("spec", {}).get("selector")
    return selector if isinstance(selector, dict) and selector and all(
        isinstance(k, str) and isinstance(v, str) for k, v in selector.items()) else None


def _labels(obj):
    labels = obj.get("spec", {}).get("template", {}).get("metadata", {}).get("labels")
    return labels if isinstance(labels, dict) else None


def _count(obj, ready=False):
    value = obj.get("status", {}).get("readyReplicas", 0) if ready else obj.get("spec", {}).get("replicas")
    return value if type(value) is int and value >= 0 else None


def _match(selector, labels):
    return None if selector is None or labels is None else all(labels.get(k) == v for k, v in selector.items())


def _aggregate(paths, unresolved=False):
    if any(p["status"] == "eligible" for p in paths):
        return "eligible"
    if unresolved or any(p["status"] == "unknown" for p in paths):
        return "unknown"
    return "blocked"


def _paths(graph, objects, services, entry, owners, now, changed_templates=()):
    """Enumerate actual selector → app → store Service → owner paths."""
    apps = [e for e in graph.entities if e.kind == "Deployment" and e.role == "deployment"]
    stores = [e for e in graph.entities if e.kind == "Deployment" and e.role == "shared_dependency"]
    by_name = {e.name: e for e in stores}
    selector = _selector(objects[entry]) if entry in objects else None
    unresolved = selector is None or owners is None
    paths, routed, route_unknown = [], [], selector is None
    for app in apps:
        obj = objects.get(app.id)
        matches = _match(selector, _labels(obj)) if obj else None
        if matches is None:
            unresolved = route_unknown = True
            continue
        if not matches:
            continue
        routed.append(app.id)
        base = {"deployment": app.id, "store": None, "status": "unknown", "reasons": []}
        desired = _count(obj)
        app_blockers, app_unknown = [], []
        if desired == 0:
            app_blockers.append("app_desired_replicas_zero:" + app.id)
        elif desired is None:
            app_unknown.append("app_desired_replicas_unknown:" + app.id)
        host = _host(obj.get("spec", {}).get("template", {}), app.namespace)
        service_owner = by_name.get(host)
        service = services.get(service_owner.id) if service_owner else None
        db_selector = _selector(service) if service else None
        if db_selector is None:
            paths.append({**base, "status": "blocked" if app_blockers else "unknown",
                          "reasons": app_blockers + app_unknown + ["store_binding_or_service_unknown:" + app.id]})
            continue
        found = False
        for store in stores:
            db = objects.get(store.id)
            selected = _match(db_selector, _labels(db)) if db else None
            if selected is None:
                paths.append({**base, "status": "blocked" if app_blockers else "unknown",
                              "reasons": app_blockers + app_unknown + ["store_route_unknown:" + store.id]})
                found = True
                continue
            if not selected:
                continue
            found = True
            reasons, blockers = list(app_unknown), list(app_blockers)
            if owners is None:
                reasons.append("state_owner_unknown")
            elif store.id not in owners:
                blockers.append("state_owner_mismatch:" + store.id)
            if _count(db) == 0:
                blockers.append("store_desired_replicas_zero:" + store.id)
            elif _count(db) is None:
                reasons.append("store_desired_replicas_unknown:" + store.id)
            if _count(db, ready=True) in (None, 0):
                reasons.append("store_readiness_unknown:" + store.id)
            if _fact(graph, store.id, "healthy", now) is False:
                reasons.append("store_health_not_established:" + store.id)
            if app.id not in changed_templates:
                if _count(obj, ready=True) in (None, 0):
                    reasons.append("app_readiness_unknown:" + app.id)
                if _fact(graph, app.id, "healthy", now) is False:
                    reasons.append("app_health_not_established:" + app.id)
            paths.append({**base, "store": store.id, "store_service": service_owner.id,
                          "status": "blocked" if blockers else "unknown" if reasons else "eligible",
                          "reasons": blockers + reasons})
        if not found:
            paths.append({**base, "status": "blocked", "reasons": ["store_selector_matches_no_deployment:" + host]})
    reasons = sorted({r for p in paths for r in p["reasons"]})
    if not routed:
        reasons.append("business_route_unknown" if route_unknown else "business_selector_matches_no_deployment")
    if owners is None:
        reasons.append("state_owner_unknown")
    return {"status": _aggregate(paths, unresolved), "paths": paths, "routed": routed,
            "route_unknown": route_unknown, "reasons": sorted(set(reasons))}


def _reference(graph, action, policy, snapshot, objects, services, projected, paths, now):
    if action is None or action.operation != "rollback_version":
        return {"status": "not_required", "reasons": []}
    ref = snapshot.get("reference") or {}
    baseline = ref.get("baseline_context") or {}
    reasons, unknown = [], []
    stamp, ttl = ref.get("observed_at"), ref.get("ttl_seconds", 600)
    if (not number(stamp) or not number(ttl) or ttl <= 0
            or not stamp <= now <= stamp + ttl):
        unknown.append("historical_measurement_expired_or_missing")
    if ref.get("healthy") is not True or not number(ref.get("capacity")) or ref["capacity"] < 0:
        unknown.append("historical_measurement_missing")
    if baseline.get("schema") != "action_configuration_v1":
        return {"status": "unknown", "reasons": unknown + ["baseline_context_missing"]}
    target = graph.entity(action.target)
    old, error = _receipt(baseline, "objects", target, now, historical=True)
    if error:
        unknown.append("baseline_" + error)
    else:
        template = (action_option(action, policy) or {}).get("template")
        if old.get("spec", {}).get("template") != template:
            reasons.append("reference_did_not_measure_approved_template")
        if _count(old) != _count(projected.get(action.target, {})):
            reasons.append("reference_app_replica_configuration_changed")
    load = _fact(graph, action.target, "load", now)
    if load is None:
        load = _fact(graph, action.destination, "load", now)
    old_load = baseline.get("load_by_entity", {}).get(action.target)
    if not number(load) or not number(old_load) or load < 0 or old_load < 0:
        unknown.append("reference_or_current_load_missing")
    elif load > old_load or number(ref.get("capacity")) and policy.capacity_margin * load > ref["capacity"]:
        reasons.append("reference_load_or_capacity_not_applicable")
    if baseline.get("owner_entities") != sorted(_owners(graph, now) or []):
        reasons.append("reference_state_owner_changed")
    dependencies = {p["store"] for p in paths["paths"] if p.get("store") and p["deployment"] == action.target}
    service_ids = {p["store_service"] for p in paths["paths"] if p.get("store_service") and p["deployment"] == action.target}
    if not dependencies or not service_ids:
        unknown.append("projected_reference_dependencies_unknown")
    for group, keys, current in (("objects", dependencies, objects), ("store_services", service_ids, services)):
        for key in sorted(keys):
            old_dep, error = _receipt(baseline, group, graph.entity(key), now,
                                      historical=True, service=group == "store_services")
            live = current.get(key)
            if error or live is None:
                unknown.append("reference_dependency_missing:" + key)
            elif (old_dep["metadata"]["uid"] != live["metadata"]["uid"]
                  or old_dep.get("spec") != live.get("spec")):
                reasons.append("reference_dependency_configuration_changed:" + key)
    # Dynamic GET readiness is checked in paths; a historical healthy bit does
    # not override a stopped, unready, or presently failing required store.
    return {"status": "inapplicable" if reasons else "unknown" if unknown else "applicable",
            "reasons": reasons + unknown, "load": old_load}


def project_action_context(graph, plan, policy, snapshot, now=None):
    """Apply the compiler's approved mutation locally and inspect its paths."""
    now = time.time() if now is None else now
    features = dict.fromkeys(FEATURE_NAMES)
    errors, required = [], set()
    if (not isinstance(snapshot, dict) or snapshot.get("schema") != "action_configuration_v1"
            or not number(snapshot.get("captured_at")) or snapshot["captured_at"] > now):
        snapshot = {}
        errors.append("configuration_snapshot_missing")
    objects, services = {}, {}
    for entity in graph.entities:
        if entity.kind not in ("Deployment", "Service"):
            continue
        obj, error = _receipt(snapshot, "objects", entity, now)
        if error:
            errors.append(error)
            required.add("configuration:" + entity.id)
        else:
            objects[entity.id] = copy.deepcopy(obj)
        if entity.role == "shared_dependency" and entity.kind == "Deployment":
            obj, error = _receipt(snapshot, "store_services", entity, now, service=True)
            if error:
                errors.append(error)
                required.add("store_service:" + entity.id)
            else:
                services[entity.id] = copy.deepcopy(obj)
    entries = [e.id for e in graph.entities if e.id in graph.goals and e.kind == "Service"]
    entry = entries[0] if len(entries) == 1 else None
    owners = _owners(graph, now)
    if owners is None:
        required.add("state_owner")
    projected = copy.deepcopy(objects)
    action = plan.actions[0] if len(plan.actions) == 1 else None
    changed = set()
    if len(plan.actions) > 1:
        errors.append("only_single_action_projection_supported")
    elif action is not None:
        if action.target in projected:
            try:
                mutation = compile_mutation(action, graph, policy, projected[action.target])
                projected[action.target]["spec"][mutation.path.rsplit("/", 1)[-1]] = mutation.after
                if mutation.path == "/spec/template" and mutation.before != mutation.after:
                    changed.add(action.target)
            except (ValueError, KeyError, TypeError) as error:
                errors.append("mutation_not_projectable:" + str(error))
        else:
            errors.append("mutation_target_missing")
    before = _paths(graph, objects, services, entry, owners, now)
    after = _paths(graph, projected, services, entry, owners, now, changed)
    reference = _reference(graph, action, policy, snapshot, objects, services, projected, after, now)

    def total(keys, table, ready=False):
        values = [_count(table[k], ready=ready) for k in keys if k in table]
        return float(sum(values)) if len(values) == len(keys) and values and all(v is not None for v in values) else None

    def approved(keys, table):
        answers = []
        for key in keys:
            templates = [v.get("template") for v in policy.rollback_options.get(key, {}).values()]
            if key in table and templates:
                answers.append(table[key].get("spec", {}).get("template") in templates)
        return float(any(answers)) if answers else None

    for prefix, paths, table in (("current", before, objects), ("projected", after, projected)):
        features[prefix + "_route_matches"] = 1. if paths["routed"] else None if paths["route_unknown"] else 0.
        features[prefix + "_routed_replicas"] = total(paths["routed"], table)
        features[prefix + "_template_matches_approved"] = approved(paths["routed"], table)
        path_stores = {p["store"] for p in paths["paths"] if p.get("store")}
        features[prefix + "_goal_owner_match"] = float(bool(path_stores & owners)) if owners and path_stores else None
        features[prefix + "_required_store_replicas"] = total(path_stores, table)
    features["current_routed_ready_replicas"] = total(before["routed"], objects, ready=True)
    current_stores = {p["store"] for p in before["paths"] if p.get("store")}
    features["current_required_store_ready_replicas"] = total(current_stores, objects, ready=True)
    features["projected_required_store_route_matches"] = (
        1. if any(p.get("store") for p in after["paths"]) else
        0. if any(r.startswith("store_selector_matches_no_deployment:") for r in after["reasons"]) else None)
    target = (action.target if action and action.operation != "restore_selector" else
              action.destination if action else before["routed"][0] if len(before["routed"]) == 1 else None)
    if target in objects:
        features["target_desired_replicas"] = _count(objects[target])
        features["target_ready_replicas"] = _count(objects[target], ready=True)
    for variable in ("healthy", "capacity"):
        value = _fact(graph, target, variable, now)
        features["target_" + variable] = float(value) if type(value) is bool or number(value) else None
        if target and value is None:
            required.add(target + ":" + variable)
    load = _fact(graph, target, "load", now)
    if load is None:
        candidates = [snapshot.get("load_by_entity", {}).get(k) for k in before["routed"]]
        load = max(candidates) if candidates and all(number(v) for v in candidates) else None
    features["offered_load"] = load
    features["entry_error_rate"] = _fact(graph, entry, "error_rate", now)
    features["entry_latency_ms"] = _fact(graph, entry, "latency_ms", now)
    features.update(replica_delta=0., template_changed=0., db_binding_changed=0., selector_changed=0.)
    if action and action.target in objects:
        old, new = objects[action.target], projected[action.target]
        if action.operation == "scale":
            features["replica_delta"] = float(_count(new) - _count(old)) if _count(new) is not None and _count(old) is not None else None
        elif action.operation == "rollback_version":
            old_template, new_template = old.get("spec", {}).get("template", {}), new.get("spec", {}).get("template", {})
            features["template_changed"] = float(old_template != new_template)
            namespace = graph.entity(action.target).namespace
            old_host, new_host = _host(old_template, namespace), _host(new_template, namespace)
            features["db_binding_changed"] = float(old_host != new_host) if old_host and new_host else None
        else:
            features["selector_changed"] = float(_selector(old) != _selector(new))
    ref = snapshot.get("reference") or {}
    features["reference_applicable"] = (1. if reference["status"] == "applicable" else
                                         0. if reference["status"] == "inapplicable" else None)
    features["reference_capacity"] = ref.get("capacity") if number(ref.get("capacity")) else None
    features["reference_load"] = reference.get("load")
    status = after["status"]
    reasons = errors + after["reasons"]
    if errors:
        status = "unknown"
    elif action and action.operation == "rollback_version" and status == "eligible" and reference["status"] != "applicable":
        status = "unknown"
    if action and action.operation == "rollback_version" and reference["status"] != "applicable":
        reasons += reference["reasons"]
        required.add("approved_reference_context")
    if not plan.actions:
        # Safe fallback means permission to refrain from writing. Preserve the
        # current structural failure and all feature values for model fitting.
        status = "eligible"
    return {"schema": "action_context_v1", "status": status, "reasons": sorted(set(reasons)),
            "features": features, "reference_status": reference,
            "required_observations": sorted(required), "current_status": before["status"],
            "projected_status": after["status"], "paths": after["paths"],
            "captured_at": snapshot.get("captured_at"), "evaluated_at": now,
            "scope": "Necessary configuration paths for a registered single-owner Station/Cart goal; not recovery."}
