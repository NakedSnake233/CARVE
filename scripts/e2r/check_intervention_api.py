#!/usr/bin/env python3
"""Validate three operation payloads against a real API using server dry-run only.

The rollback payload is a roundtrip of the currently observed template, not
evidence of a real rollback. No readiness/capacity or recovery claim is inferred.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from methods.E2R.intervention.contracts import Policy, compile_mutation, field_value
from methods.E2R.intervention.platform import KubectlClient
from methods.E2R.intervention.schema import Action, Entity, EvidenceGraph, Relation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--kubeconfig", default="/etc/rancher/k3s/k3s.yaml")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError("preserve the previous API check; choose a new output path")
    client = KubectlClient(args.namespace, args.kubeconfig)
    service = client.get("Service", args.service)
    deployment = client.get("Deployment", args.deployment)
    selector = service["spec"].get("selector")
    labels = deployment["spec"]["template"]["metadata"].get("labels", {})
    if not selector or any(labels.get(k) != v for k, v in selector.items()):
        raise ValueError("observed Service selector does not select this Deployment template")
    entities = [Entity(key, role, value["kind"], args.namespace, value["metadata"]["name"],
                       value["metadata"]["uid"], value["metadata"]["resourceVersion"])
                for key, role, value in (("service", "service", service), ("deployment", "deployment", deployment))]
    now = time.time()
    graph = EvidenceGraph(now, entities, [], [Relation("service", "deployment", "routes_to", now, 60)], ["service"])
    replicas = deployment["spec"]["replicas"]
    policy = Policy(args.namespace, ["service", "deployment"],
        selector_options={"service": {"observed": {"selector": selector, "destination": "deployment"}}},
        rollback_options={"deployment": {"observed": {"template": deployment["spec"]["template"], "destination": "deployment"}}},
        max_replicas={"deployment": replicas+1})
    actions = [Action("restore_selector", "service", {"option": "observed"}, "deployment"),
               Action("scale", "deployment", {"replicas": replicas+1}),
               Action("rollback_version", "deployment", {"option": "observed"}, "deployment")]
    rows = []
    for action in actions:
        node = graph.entity(action.target)
        before = client.get(node.kind, node.name)
        mutation = compile_mutation(action, graph, policy, before)
        started = time.perf_counter()
        accepted = client.patch(node.kind, node.name, mutation.patch, dry_run=True)
        after = client.get(node.kind, node.name)
        rows.append({"operation": action.operation, "field": mutation.path, "server_dry_run": True,
            "accepted_expected_field": field_value(accepted, mutation.path) == mutation.after,
            "real_controlled_field_unchanged": field_value(after, mutation.path) == mutation.before,
            "uid_unchanged": after["metadata"]["uid"] == mutation.uid,
            "seconds": time.perf_counter()-started})
    report = {"schema": "e2r_intervention_api_check_v1", "namespace": args.namespace,
        "service": args.service, "deployment": args.deployment, "checks": rows,
        "status": "passed" if all(r["accepted_expected_field"] and r["real_controlled_field_unchanged"] and r["uid_unchanged"] for r in rows) else "failed",
        "scope": "actual API admission/field semantics only; no control-loop execution or recovery evidence",
        "rollback_scope": "currently observed template roundtrip, not a real historical-version rollback"}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": report["status"], "namespace": args.namespace, "operations": len(rows), "server_dry_run_only": True}))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
