#!/usr/bin/env python3
"""In-cluster restore worker for a single bounded Deployment config fault.

Run from a Kubernetes Job with a ServiceAccount allowed to get/patch only the
named Deployment. It survives collector exit and node reboot through Job
restarts. Transient API errors retry until exact resource restoration verifies.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


TOKEN = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
CA = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
HOST = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
PORT = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
LEASE_KEY = "e2r.research/restore-lease"
LEASE_POINTER = LEASE_KEY.replace("~", "~0").replace("/", "~1")


def endpoint(namespace: str, deployment: str) -> str:
    return f"https://{HOST}:{PORT}/apis/apps/v1/namespaces/{namespace}/deployments/{deployment}"


def request(method: str, url: str, body=None, content_type="application/json"):
    headers = {"Authorization": "Bearer " + TOKEN.read_text().strip(),
               "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = content_type
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    context = ssl.create_default_context(cafile=str(CA))
    with urllib.request.urlopen(req, timeout=10, context=context) as response:
        return json.load(response)


def first_resources(deployment: dict) -> dict:
    containers = (((deployment.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []
    return (containers[0].get("resources") or {}) if containers else {}


def lease_state(deployment: dict, payload: dict) -> str:
    meta = deployment.get("metadata") or {}
    if meta.get("uid") != payload["uid"]:
        return "uid_changed"
    containers = (((deployment.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []
    if not containers or containers[0].get("name") != payload["container"]:
        return "container_changed"
    current = (meta.get("annotations") or {}).get(LEASE_KEY)
    pod = (((deployment.get("spec") or {}).get("template") or {}).get("metadata") or {})
    pod_lease = (pod.get("annotations") or {}).get(LEASE_KEY)
    if current == payload["lease_id"] and pod_lease == payload["lease_id"]:
        return ("armed" if first_resources(deployment) == payload["injected_resources"]
                else "injected_state_changed")
    if first_resources(deployment) == payload["original_resources"] and not current and not pod_lease:
        return "restored"
    return "lease_mismatch"


def restore_patch(deployment: dict, payload: dict) -> list[dict]:
    return [
        {"op": "test", "path": "/metadata/uid", "value": payload["uid"]},
        {"op": "test", "path": "/metadata/resourceVersion",
         "value": deployment["metadata"]["resourceVersion"]},
        {"op": "test", "path": "/spec/template/spec/containers/0/name",
         "value": payload["container"]},
        {"op": "test", "path": "/metadata/annotations/" + LEASE_POINTER,
         "value": payload["lease_id"]},
        {"op": "test", "path": "/spec/template/metadata/annotations/" + LEASE_POINTER,
         "value": payload["lease_id"]},
        {"op": "test", "path": "/spec/template/spec/containers/0/resources",
         "value": payload["injected_resources"]},
        {"op": "replace", "path": "/spec/template/spec/containers/0/resources",
         "value": payload["original_resources"]},
        {"op": "remove", "path": "/metadata/annotations/" + LEASE_POINTER},
        {"op": "remove", "path": "/spec/template/metadata/annotations/" + LEASE_POINTER},
    ]


def run(payload: dict) -> int:
    if payload["deadline_epoch"] - payload["armed_epoch"] > 240 or \
            payload["deadline_epoch"] <= payload["armed_epoch"]:
        raise ValueError("restore lease must be in (0,240] seconds")
    url = endpoint(payload["namespace"], payload["deployment"])
    # The collector waits for this preflight acknowledgement before patching.
    # After a node reboot the Job may restart after injection, so both an
    # unmodified Deployment and our already-armed lease are valid states here.
    while time.time() < payload["deadline_epoch"]:
        try:
            deployment = request("GET", url)
            state = lease_state(deployment, payload)
            if state in {"restored", "armed"}:
                # Test the same JSON Patch route and narrow PATCH RBAC using a
                # Kubernetes dry-run. This mutates no Deployment field.
                request("PATCH", url + "?dryRun=All", [
                    {"op": "test", "path": "/metadata/uid", "value": payload["uid"]},
                    {"op": "test", "path": "/spec/template/spec/containers/0/name",
                     "value": payload["container"]},
                    {"op": "replace", "path": "/spec/template/spec/containers/0/resources",
                     "value": payload["original_resources"]}],
                    content_type="application/json-patch+json")
                print(json.dumps({"status": "armed_preflight", "deployment": payload["deployment"]}),
                      flush=True)
                break
            print(json.dumps({"status": "terminal_no_mutation", "reason": state,
                              "deployment": payload["deployment"]}), flush=True)
            return 0
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            print(json.dumps({"status": "preflight_retry", "error_type": type(exc).__name__}), flush=True)
            time.sleep(5)
    while True:
        remaining = payload["deadline_epoch"] - time.time()
        if remaining <= 0:
            break
        time.sleep(min(5.0, remaining))
    while True:
        try:
            deployment = request("GET", url)
            state = lease_state(deployment, payload)
            if state == "restored":
                print(json.dumps({"status": "restored_verified", "deployment": payload["deployment"]}),
                      flush=True)
                return 0
            if state == "injected_state_changed":
                print(json.dumps({"status": "manual_review_required",
                                  "reason": state, "deployment": payload["deployment"]}),
                      flush=True)
                time.sleep(5)
                continue
            if state in {"uid_changed", "container_changed", "lease_mismatch",
                         }:
                print(json.dumps({"status": "terminal_no_mutation", "reason": state,
                                  "deployment": payload["deployment"]}),
                      flush=True)
                return 0
            request("PATCH", url, restore_patch(deployment, payload),
                    content_type="application/json-patch+json")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            print(json.dumps({"status": "retry", "error_type": type(exc).__name__}), flush=True)
        time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(run(json.loads(Path(sys.argv[1]).read_text())))
