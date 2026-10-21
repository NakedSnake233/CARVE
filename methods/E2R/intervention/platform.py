"""Kubernetes adapters. Model outputs never become shell commands or probe URLs."""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from methods.PlanRCAThinkRemed.cluster_client import ClusterClientContext
from .contracts import Policy, check_plan, compile_mutation, compensation, feasible, field_value
from .schema import Entity, EvidenceGraph, Fact, Plan, Relation, Witness, GraphObservation, merge_observation, strict

RESOURCE_TYPES = {"Service": "service", "Deployment": "deployment", "Pod": "pod",
                  "EndpointSlice": "endpointslice", "HorizontalPodAutoscaler": "hpa"}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", value):
        raise ValueError("invalid Kubernetes resource identifier")
    return value


class KubectlClient:
    def __init__(self, namespace, kubeconfig=None, timeout=15):
        self.namespace = identifier(namespace)
        self.context = ClusterClientContext(kubeconfig) if kubeconfig else ClusterClientContext()
        self.timeout = timeout

    def _call(self, args):
        result = subprocess.run(["kubectl", "--namespace", self.namespace,
                                 "--request-timeout", f"{self.timeout}s", *args],
                                env=self.context.environment(), capture_output=True, text=True,
                                timeout=self.timeout + 5, check=False)
        if result.returncode:
            raise RuntimeError("Kubernetes command failed: " + result.stderr[-1000:])
        return json.loads(result.stdout)

    def _call_cluster(self, args):
        """Read cluster-scoped resources without accidentally mixing namespace flags."""
        result=subprocess.run(["kubectl","--request-timeout",f"{self.timeout}s",*args],
                              env=self.context.environment(),capture_output=True,text=True,timeout=self.timeout+5,check=False)
        if result.returncode:
            raise RuntimeError("Kubernetes cluster command failed: "+result.stderr[-1000:])
        return json.loads(result.stdout)

    def get(self, kind, name):
        return self._call(["get", RESOURCE_TYPES[kind], identifier(name), "-o", "json"])

    def list(self, kind, selector=None):
        args = ["get", RESOURCE_TYPES[kind], "-o", "json"]
        if selector:
            args.extend(["--selector", selector])
        return self._call(args)["items"]

    def patch(self, kind, name, patch, *, dry_run=False):
        args = ["patch", RESOURCE_TYPES[kind], identifier(name), "--type=json", "--patch",
                json.dumps(patch, allow_nan=False), "-o", "json"]
        if dry_run:
            args.append("--dry-run=server")
        return self._call(args)


@dataclass(frozen=True)
class Probe:
    id: str
    entity: str
    variable: str
    kind: str
    possible_values: list
    cost: float = 1.0
    timeout: float = 5.0
    ttl: float = 30.0
    url: str = ""
    json_path: tuple = ()
    resource_kind: str = ""
    resource_name: str = ""
    probabilities: tuple = ()
    phase: str = "pre"

    def __post_init__(self):
        if (not self.id or self.phase not in ("pre", "post") or self.kind not in ("http_json", "graph_json", "kubernetes", "native", "native_graph") or not self.possible_values
                or self.cost <= 0 or self.timeout <= 0 or self.ttl <= 0
                or self.probabilities and (len(self.probabilities) != len(self.possible_values)
                    or any(p < 0 for p in self.probabilities) or abs(sum(self.probabilities)-1) > 1e-6)):
            raise ValueError("invalid trusted probe")
        if self.kind in ("http_json", "graph_json") and not self.url.startswith(("http://", "https://")):
            raise ValueError("probe URL must be explicitly configured HTTP(S)")
        if self.kind == "kubernetes":
            if self.resource_kind not in RESOURCE_TYPES:
                raise ValueError("probe resource kind is not read-allowlisted")
            identifier(self.resource_name)


class LiveProvider:
    """Inventory declares roles/constraints; current facts always identify their source."""
    def __init__(self, inventory: dict, client: KubectlClient):
        self.inventory, self.client = inventory, client
        if inventory["namespace"] != client.namespace:
            raise ValueError("inventory and client namespaces differ")
        registered = [strict(Probe, p) for p in inventory.get("probes", [])]
        self.probes = [p for p in registered if p.phase == "pre"]
        self.post_probes = [p for p in registered if p.phase == "post"]
        if len({p.id for p in registered}) != len(registered):
            raise ValueError("duplicate registered probes")

    def collect(self):
        entities, facts, relations = [], [], []
        ttl = self.inventory.get("ttl", 30.0)
        for entry in self.inventory["entities"]:
            e = strict(Entity, entry)
            stamp = time.time()
            if e.kind:
                if e.namespace != self.client.namespace:
                    raise ValueError("cross-namespace inventory target")
                raw = self.client.get(e.kind, e.name)
                stamp = time.time()
                meta = raw["metadata"]
                e = Entity(e.id, e.role, e.kind, e.namespace, e.name, meta["uid"], meta["resourceVersion"], binding=e.binding)
                if e.kind == "Deployment":
                    values = {"replicas": raw["spec"].get("replicas"),
                              "ready_replicas": raw.get("status", {}).get("readyReplicas", 0)}
                    try:
                        hpas = self.client.list("HorizontalPodAutoscaler")
                        values["hpa_absent"] = not any(x.get("spec", {}).get("scaleTargetRef", {}).get("name") == e.name for x in hpas)
                    except (RuntimeError, subprocess.SubprocessError):
                        values["hpa_absent"] = None
                    for key, value in values.items():
                        facts.append(Fact(f"{e.id}:{key}:{stamp}", e.id, key, value, stamp, ttl, "kubernetes", uid=e.uid))
                elif e.kind == "Service":
                    ready = self.ready_endpoints(e.name, None)
                    facts.append(Fact(f"{e.id}:endpoints:{stamp}", e.id, "endpoints", ready, stamp, ttl, "EndpointSlice+Pod", uid=e.uid))
            entities.append(e)
        now = time.time()
        ids = {e.id: e for e in entities}
        for declaration in self.inventory.get("contract_facts", []):
            # These are operator attestations, visibly distinct from measurements.
            facts.append(Fact(f"contract:{declaration['entity']}:{declaration['variable']}:{now}",
                              declaration["entity"], declaration["variable"], declaration["value"],
                              declaration["observed_at"], declaration.get("ttl", ttl),
                              "operator_contract", "contract", ids[declaration["entity"]].uid))
        for edge in self.inventory.get("relations", []):
            relations.append(Relation(edge["source"], edge["target"], edge["kind"],
                                      edge["observed_at"], edge.get("ttl", ttl), "configuration"))
        return EvidenceGraph(now, entities, facts, relations, list(self.inventory["goals"]))

    def _read_probe(self, spec):
        if spec not in self.probes + self.post_probes:
            raise ValueError("unregistered probe")
        if spec.kind in ("native", "native_graph"):
            raise ValueError("native probes require the registered native-study adapter")
        started = time.monotonic()
        if spec.kind == "kubernetes":
            raw = self.client.get(spec.resource_kind, spec.resource_name)
        else:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(urllib.request.Request(spec.url, headers={"User-Agent": "E2R-intervention-probe"}),
                             timeout=spec.timeout) as response:
                payload = response.read(1_000_001)
                if len(payload) > 1_000_000:
                    raise ValueError("probe response exceeds registered observation limit")
                raw = json.loads(payload)
        value = raw
        for key in spec.json_path:
            value = value[key]
        if time.monotonic()-started > spec.timeout:
            raise TimeoutError("probe exceeded its declared latency budget")
        stamp = time.time()
        return value, stamp

    def probe(self, spec: Probe, graph: EvidenceGraph) -> Fact:
        if spec not in self.probes:
            raise ValueError("post-action verification probes cannot enter decision inputs")
        value, stamp = self._read_probe(spec)
        if spec.kind == "graph_json":
            return graph_observation(value, spec, graph, stamp, self.client.namespace)
        return Fact(f"probe:{spec.id}:{stamp}", spec.entity, spec.variable, value, stamp, spec.ttl,
                    "probe:" + spec.id, uid=graph.entity(spec.entity).uid)

    def observe_post(self, spec):
        if spec not in self.post_probes:
            raise ValueError("independent post-action check must be registered separately")
        value, stamp = self._read_probe(spec)
        return {"probe_id": spec.id, "value": value, "observed_at": stamp, "source": "independent_post_probe"}

    def refresh_probes(self, graph, action):
        from .contracts import required_variables
        required = required_variables(graph, action)
        selected = []
        for probe in self.probes:
            supplied = {(probe.entity, probe.variable)}
            if probe.kind in ("graph_json", "native_graph"):
                supplied |= {(f["entity"], f["variable"]) for value in probe.possible_values for f in value.get("facts", [])}
            if supplied & required:
                selected.append(probe)
        return selected

    def refresh(self, previous, action):
        began = time.perf_counter()
        graph = self.collect()
        observations = []
        previous_relations = {(r.source, r.target, r.kind): r for r in previous.relations}
        fresh_relations = [r for r in graph.relations if (r.source, r.target, r.kind) not in previous_relations
                           or r.observed_at >= previous_relations[(r.source, r.target, r.kind)].observed_at]
        graph = merge_observation(previous, GraphObservation(graph.entities, graph.facts, fresh_relations), graph.at)
        # Keep observed history only while it remains bound to the same resource.
        ids = {e.id: e for e in graph.entities}
        existing = {f.id for f in graph.facts}
        graph.facts = [f for f in graph.facts if f.entity in ids and (not ids[f.entity].uid or f.uid == ids[f.entity].uid)]
        for probe in self.refresh_probes(graph, action):
            started = time.perf_counter()
            fact = self.probe(probe, graph)
            if isinstance(fact, GraphObservation):
                graph = merge_observation(graph, fact, time.time())
            else:
                graph.at = max(time.time(), fact.observed_at)
                graph.facts.append(fact)
            observations.append({"id": probe.id, "cost": probe.cost, "seconds": time.perf_counter()-started})
        graph.at = time.time()
        graph.__post_init__()
        self.last_refresh = {"seconds": time.perf_counter()-began, "probes": observations,
                             "registered_probe_cost": sum(p["cost"] for p in observations)}
        return graph

    def ready_endpoints(self, service, expected_selector):
        svc = self.client.get("Service", service)
        if svc.get("spec", {}).get("publishNotReadyAddresses") is True:
            return None
        slices = self.client.list("EndpointSlice", "kubernetes.io/service-name=" + identifier(service))
        ready = 0
        seen = set()
        for slice_ in slices:
            for endpoint in slice_.get("endpoints") or []:
                condition = endpoint.get("conditions") or {}
                ref = endpoint.get("targetRef") or {}
                if condition.get("ready") is not True or condition.get("terminating") is True or ref.get("kind") != "Pod":
                    continue
                pod = self.client.get("Pod", ref["name"])
                meta = pod.get("metadata", {})
                if ref.get("uid") != meta.get("uid") or meta.get("uid") in seen:
                    continue
                labels = meta.get("labels", {})
                if expected_selector and any(labels.get(k) != v for k, v in expected_selector.items()):
                    continue
                if not any(c.get("type") == "Ready" and c.get("status") == "True" for c in pod.get("status", {}).get("conditions", [])):
                    continue
                ready += 1
                seen.add(meta["uid"])
        return ready


def graph_observation(payload, spec, graph, stamp, namespace):
    if not isinstance(payload, dict) or set(payload)-{"entities", "facts", "relations"}:
        raise ValueError("structural probe must return a typed graph observation")
    entities = [strict(Entity, e) for e in payload.get("entities", [])]
    if any(e.kind and e.namespace != namespace for e in entities):
        raise ValueError("structural probe expanded outside its registered namespace")
    lookup = {e.id: e for e in graph.entities+entities}
    facts = []
    for index, raw in enumerate(payload.get("facts", [])):
        if set(raw) != {"entity", "variable", "value"} or raw["entity"] not in lookup:
            raise ValueError("invalid structural probe fact")
        facts.append(Fact(f"graphprobe:{spec.id}:{stamp}:{index}", raw["entity"], raw["variable"], raw["value"],
                          stamp, spec.ttl, "probe:"+spec.id, uid=lookup[raw["entity"]].uid))
    relations = []
    for raw in payload.get("relations", []):
        if set(raw)-{"source", "target", "kind", "validity"}:
            raise ValueError("invalid structural relation")
        relations.append(Relation(raw["source"], raw["target"], raw["kind"], stamp, spec.ttl,
                                   "observed", tuple(f.id for f in facts), raw.get("validity", "present")))
    observation = GraphObservation(entities, facts, relations)
    merge_observation(graph, observation, stamp)
    return observation


class Executor:
    def __init__(self, client, provider, policy: Policy, journal, *, convergence_timeout=60):
        if client.namespace != policy.namespace:
            raise ValueError("execution namespace mismatch")
        self.client, self.provider, self.policy = client, provider, policy
        self.journal = Path(journal)
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        self.journal.touch(mode=0o600, exist_ok=False)
        self.convergence_timeout = convergence_timeout
        self.applied = []

    def record(self, event):
        with self.journal.open("a") as stream:
            stream.write(json.dumps({"time": time.time(), **event}, allow_nan=False) + "\n")
            stream.flush()
            import os
            os.fsync(stream.fileno())

    def _wait_controller(self, action, mutation):
        deadline = time.monotonic() + self.convergence_timeout
        while time.monotonic() < deadline:
            raw = self.client.get(mutation.kind, mutation.name)
            if raw["metadata"]["uid"] != mutation.uid or field_value(raw, mutation.path) != mutation.after:
                return False
            if mutation.kind == "Service":
                if (self.provider.ready_endpoints(mutation.name, mutation.after) or 0) > 0:
                    return True
            else:
                status, spec = raw.get("status", {}), raw["spec"]
                desired = spec.get("replicas", 1)
                if (status.get("observedGeneration", 0) >= raw["metadata"].get("generation", 1)
                        and status.get("updatedReplicas", 0) == desired
                        and status.get("readyReplicas", 0) == desired
                        and status.get("availableReplicas", 0) == desired):
                    return True
            time.sleep(min(1.0, max(0.0, deadline-time.monotonic())))
        return False

    def execute(self, graph, plan, *, dry_run=True, refresh=None, authorize=None):
        result = {"plan_id": plan.id, "status": "no_action" if not plan.actions else "pending",
                  "dry_run": dry_run, "steps": [], "refreshes": [], "timings_seconds": {}, "executed": False}
        if not plan.actions:
            return result
        original_uids = {e.id: e.uid for e in graph.entities}
        initial = check_plan(graph, plan, self.policy)
        if not feasible(initial):
            result.update(status="rejected", checks=[asdict(c) for c in initial])
            return result
        for index, action in enumerate(plan.actions):
            # A preceding desired-state write is never accepted as fresh readiness evidence.
            if not dry_run:
                refresh_started = time.perf_counter()
                graph = refresh() if refresh else self.provider.refresh(graph, action)
                elapsed = time.perf_counter()-refresh_started
                result["timings_seconds"][f"precondition_refresh_{index}"] = elapsed
                evidence = {"step": index, "seconds": elapsed, "graph": graph.to_dict(),
                            "observation_cost": getattr(self.provider, "last_refresh", None)}
                result["refreshes"].append(evidence)
                self.record({"event": "mandatory_refresh", **evidence})
                if any(graph.entity(k).uid != original_uids[k] for k in (action.target, action.destination) if k):
                    result.update(status="rejected", error="resource_identity_changed_requires_replanning")
                    break
            checks = check_plan(graph, Plan(plan.id, (action,)), self.policy)
            if not feasible(checks, allow_deferred=False):
                result.update(status="rejected", checks=[asdict(c) for c in checks])
                break
            node = graph.entity(action.target)
            live = self.client.get(node.kind, node.name)
            mutation = compile_mutation(action, graph, self.policy, live)
            if not dry_run and authorize is not None:
                approval = authorize(graph, Plan(plan.id, plan.actions[index:]))
                result["refreshes"][-1]["empirical_recheck"] = approval
                self.record({"event": "empirical_recheck", "step": index, "approval": approval})
                if approval.get("approved") is not True:
                    result.update(status="empirical_recheck_rejected")
                    break
            self.record({"event": "prepared", "plan_id": plan.id, "step": index, "mutation": asdict(mutation), "dry_run": dry_run})
            started = time.perf_counter()
            try:
                accepted = self.client.patch(node.kind, node.name, mutation.patch, dry_run=dry_run)
            except Exception as exc:
                if not dry_run:
                    self.applied.append(mutation)
                self.record({"event": "write_outcome_unknown", "step": index, "error": type(exc).__name__})
                result.update(status="execution_unknown", error=type(exc).__name__)
                break
            write_ok = accepted.get("metadata", {}).get("uid") == node.uid and field_value(accepted, mutation.path) == mutation.after
            elapsed = time.perf_counter() - started
            step = {"index": index, "operation": action.operation, "target": action.target,
                    "field": mutation.path, "write_verified": write_ok, "write_seconds": elapsed}
            result["steps"].append(step)
            result["timings_seconds"][f"write_{index}"] = elapsed
            self.record({"event": "api_accepted", "step": index, "write_verified": write_ok, "dry_run": dry_run})
            if not dry_run:
                if accepted.get("metadata", {}).get("uid") == mutation.uid:
                    mutation.after = field_value(accepted, mutation.path)
                self.applied.append(mutation)
            if not write_ok:
                result["status"] = "write_deviation"
                break
            if not dry_run:
                started = time.perf_counter()
                converged = self._wait_controller(action, mutation)
                step["controller_converged"] = converged
                result["timings_seconds"][f"controller_{index}"] = time.perf_counter() - started
                if not converged:
                    result["status"] = "controller_failed"
                    break
        else:
            result["status"] = "dry_run" if dry_run else "executed"
            result["executed"] = not dry_run
        result["all_writes_applied"] = (not dry_run and len(result["steps"])==len(plan.actions)
                                         and all(step.get("write_verified") for step in result["steps"]))
        self.record({"event": "execution_result", "result": result})
        return result

    def compensate(self):
        results = []
        for mutation in reversed(self.applied):
            try:
                live = self.client.get(mutation.kind, mutation.name)
                if live.get("metadata", {}).get("uid") == mutation.uid and field_value(live, mutation.path) == mutation.before:
                    results.append({"entity": mutation.entity, "restored": True, "already_original": True})
                    self.record({"event": "compensation", **results[-1]})
                    continue
                accepted = self.client.patch(mutation.kind, mutation.name, compensation(mutation, live))
                ok = accepted.get("metadata", {}).get("uid") == mutation.uid and field_value(accepted, mutation.path) == mutation.before
                row = {"entity": mutation.entity, "restored": ok}
            except Exception as exc:
                row = {"entity": mutation.entity, "restored": False, "error": type(exc).__name__}
            results.append(row)
            self.record({"event": "compensation", **row})
            if not row["restored"]:
                break
        if len(results) == len(self.applied) and all(r["restored"] for r in results):
            self.applied.clear()
        return results
