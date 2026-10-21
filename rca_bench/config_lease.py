"""Namespace-scoped persistent restore lease for live config-error injection.

The restore Job runs in `e2r-control`, outside the system under test. Its
ServiceAccount can get/patch only one named Deployment in the target namespace.
The Job verifies UID + lease annotation before restoring exact resource fields.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

import yaml

from scripts.config_restore_job_worker import LEASE_KEY


ROOT = Path(__file__).resolve().parents[1]
CONTROL_NAMESPACE = "e2r-control"
IMAGE = "python:3.10-alpine"
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


def cpu_millicores(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    try:
        return float(text[:-1]) if text.endswith("m") else float(text) * 1000.0
    except ValueError:
        return None


def original_deployment(manifest: Path, name: str) -> dict:
    for doc in yaml.safe_load_all(manifest.read_text()):
        if isinstance(doc, dict) and doc.get("kind") == "Deployment" and \
                (doc.get("metadata") or {}).get("name") == name:
            return doc
    raise ValueError(f"Deployment {name} absent from original manifest")


def kubectl_json(*args: str) -> dict:
    proc = subprocess.run(["kubectl", *args, "-o", "json"], capture_output=True,
                          text=True, timeout=20, check=True)
    return json.loads(proc.stdout)


def validate_source(namespace: str, deployment: str, manifest: Path) -> tuple[dict, dict]:
    if namespace not in {"train-ticket", "online-boutique"} or not NAME_RE.fullmatch(deployment):
        raise ValueError("config lease outside benchmark scope")
    source = original_deployment(manifest, deployment)
    expected_container = source["spec"]["template"]["spec"]["containers"][0]
    original = expected_container.get("resources") or {}
    cpu = cpu_millicores((original.get("limits") or {}).get("cpu"))
    if cpu is None or cpu <= 5:
        raise ValueError("original CPU limit is absent or already tiny")
    live = kubectl_json("get", "deployment", deployment, "-n", namespace)
    live_container = live["spec"]["template"]["spec"]["containers"][0]
    if live_container["name"] != expected_container["name"] or \
            (live_container.get("resources") or {}) != original:
        raise ValueError("live Deployment resources differ from frozen source manifest")
    if LEASE_KEY in (live.get("metadata", {}).get("annotations") or {}) or \
            LEASE_KEY in (live["spec"]["template"].get("metadata", {}).get("annotations") or {}):
        raise ValueError("Deployment already has a config restore lease")
    if not live.get("metadata", {}).get("uid"):
        raise ValueError("live Deployment UID missing")
    return live, original


def lease_objects(payload: dict, worker_source: str, name: str) -> list[dict]:
    namespace, target = payload["namespace"], payload["deployment"]
    labels = {"e2r.research/lease": payload["lease_id"]}
    return [
        {"apiVersion": "v1", "kind": "ServiceAccount",
         "metadata": {"name": name, "namespace": CONTROL_NAMESPACE, "labels": labels}},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role",
         "metadata": {"name": name, "namespace": namespace, "labels": labels},
         "rules": [{"apiGroups": ["apps"], "resources": ["deployments"],
                    "resourceNames": [target], "verbs": ["get", "patch"]}]},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding",
         "metadata": {"name": name, "namespace": namespace, "labels": labels},
         "subjects": [{"kind": "ServiceAccount", "name": name,
                       "namespace": CONTROL_NAMESPACE}],
         "roleRef": {"kind": "Role", "name": name,
                     "apiGroup": "rbac.authorization.k8s.io"}},
        {"apiVersion": "v1", "kind": "ConfigMap",
         "metadata": {"name": name, "namespace": CONTROL_NAMESPACE, "labels": labels},
         "data": {"worker.py": worker_source,
                  "payload.json": json.dumps(payload, sort_keys=True)}},
        {"apiVersion": "batch/v1", "kind": "Job",
         "metadata": {"name": name, "namespace": CONTROL_NAMESPACE, "labels": labels},
         "spec": {"backoffLimit": 2147483647, "ttlSecondsAfterFinished": 600,
                  "template": {"metadata": {"labels": {"e2r-lease": name}},
                               "spec": {"restartPolicy": "OnFailure",
                                        "serviceAccountName": name,
                                        "containers": [{"name": "restore", "image": IMAGE,
                                                        "imagePullPolicy": "IfNotPresent",
                                                        "command": ["python3", "/restore/worker.py",
                                                                    "/restore/payload.json"],
                                                        "env": [{"name": "PYTHONDONTWRITEBYTECODE",
                                                                 "value": "1"}],
                                                        "resources": {"requests": {"cpu": "20m",
                                                                                   "memory": "32Mi"},
                                                                      "limits": {"cpu": "200m",
                                                                                 "memory": "128Mi"}},
                                                        "volumeMounts": [{"name": "restore",
                                                                          "mountPath": "/restore",
                                                                          "readOnly": True}],
                                                        "securityContext": {"allowPrivilegeEscalation": False,
                                                                            "readOnlyRootFilesystem": True,
                                                                            "runAsNonRoot": True,
                                                                            "runAsUser": 65534}}],
                                        "volumes": [{"name": "restore",
                                                     "configMap": {"name": name}}]}}}},
    ]


def namespace_exists(namespace: str) -> bool:
    proc = subprocess.run(["kubectl", "get", "namespace", namespace], capture_output=True,
                          text=True, timeout=20)
    return proc.returncode == 0


def cleanup_unarmed(name: str, target_namespace: str) -> list[str]:
    """Remove a failed pre-injection lease; no SUT mutation has occurred."""
    failed = []
    for namespace, resources in ((CONTROL_NAMESPACE, ["job", "configmap", "serviceaccount"]),
                                 (target_namespace, ["rolebinding", "role"])):
        for resource in resources:
            proc = subprocess.run(["kubectl", "delete", resource, name, "-n", namespace,
                                   "--ignore-not-found=true", "--wait=true", "--timeout=30s"],
                                  capture_output=True, text=True, timeout=40)
            if proc.returncode:
                failed.append(f"{namespace}/{resource}/{name}")
    return failed


def arm(namespace: str, deployment: str, manifest: Path, *,
        duration_s: int = 240) -> dict:
    if not 60 <= duration_s <= 240:
        raise ValueError("config lease duration must be between 60 and 240 seconds")
    os.environ["KUBECONFIG"] = "/etc/rancher/k3s/k3s.yaml"
    live, original = validate_source(namespace, deployment, manifest)
    if not namespace_exists(CONTROL_NAMESPACE):
        proc = subprocess.run(["kubectl", "create", "namespace", CONTROL_NAMESPACE],
                              capture_output=True, text=True, timeout=20)
        if proc.returncode:
            raise RuntimeError("cannot create e2r-control namespace")
    lease_id = uuid.uuid4().hex
    name = "e2r-restore-" + lease_id[:16]
    armed_epoch = time.time()
    payload = {"namespace": namespace, "deployment": deployment,
               "uid": live["metadata"]["uid"], "lease_id": lease_id,
               "container": live["spec"]["template"]["spec"]["containers"][0]["name"],
               "original_resources": original, "armed_epoch": armed_epoch,
               "injected_resources": {"requests": {"cpu": "1m", "memory": "100Mi"},
                                      "limits": {"cpu": "2m", "memory": "200Mi"}},
               "deadline_epoch": armed_epoch + duration_s}
    worker = (ROOT / "scripts/config_restore_job_worker.py").read_text()
    manifest_text = yaml.safe_dump_all(lease_objects(payload, worker, name))
    try:
        proc = subprocess.run(["kubectl", "apply", "-f", "-"], input=manifest_text,
                              capture_output=True, text=True, timeout=30)
        if proc.returncode:
            raise RuntimeError("restore lease Job/RBAC could not be installed")
        # Wait for the worker's GET+dry-run PATCH acknowledgement, not merely
        # Pod Running. A slow image pull cannot consume most of the lease.
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            logs = subprocess.run(["kubectl", "logs", "-n", CONTROL_NAMESPACE,
                                   f"job/{name}", "--tail=20"], capture_output=True,
                                  text=True, timeout=15)
            if logs.returncode == 0 and '"status": "armed_preflight"' in logs.stdout:
                if payload["deadline_epoch"] - time.time() < 150:
                    raise RuntimeError("restore lease has too little time remaining")
                return {"job_name": name, "payload": payload}
            time.sleep(3)
        raise RuntimeError("restore lease Job did not acknowledge preflight")
    except Exception as exc:
        remnants = cleanup_unarmed(name, namespace)
        if remnants:
            raise RuntimeError(f"restore lease setup failed; residual controls: {remnants}") from exc
        raise


def inject_with_lease(lease: dict) -> dict:
    payload = lease["payload"]
    if payload["deadline_epoch"] - time.time() < 150:
        raise RuntimeError("restore lease expired before injection")
    job = kubectl_json("get", "job", lease["job_name"], "-n", CONTROL_NAMESPACE)
    if job.get("status", {}).get("active", 0) < 1 or job.get("status", {}).get("succeeded", 0):
        raise RuntimeError("restore lease Job is not active immediately before injection")
    namespace, name = payload["namespace"], payload["deployment"]
    live = kubectl_json("get", "deployment", name, "-n", namespace)
    if live["metadata"]["uid"] != payload["uid"] or \
            (live["spec"]["template"]["spec"]["containers"][0].get("resources") or {}) != payload["original_resources"]:
        raise ValueError("Deployment changed between lease arming and injection")
    if LEASE_KEY in (live["metadata"].get("annotations") or {}) or \
            LEASE_KEY in (live["spec"]["template"].get("metadata", {}).get("annotations") or {}):
        raise ValueError("another config restore lease appeared before injection")
    meta = dict(live["metadata"].get("annotations") or {})
    pod_meta = dict(live["spec"]["template"]["metadata"].get("annotations") or {})
    meta[LEASE_KEY] = payload["lease_id"]
    pod_meta[LEASE_KEY] = payload["lease_id"]
    patch = [{"op": "test", "path": "/metadata/uid", "value": payload["uid"]},
             {"op": "test", "path": "/metadata/resourceVersion",
              "value": live["metadata"]["resourceVersion"]},
             {"op": "add", "path": "/metadata/annotations", "value": meta},
             {"op": "add", "path": "/spec/template/metadata/annotations", "value": pod_meta},
             {"op": "replace", "path": "/spec/template/spec/containers/0/resources",
              "value": payload["injected_resources"]}]
    dry_run = subprocess.run(["kubectl", "patch", "deployment", name, "-n", namespace,
                              "--type=json", "--dry-run=server", "-p", json.dumps(patch),
                              "-o", "json"],
                             capture_output=True, text=True, timeout=20)
    if dry_run.returncode:
        raise RuntimeError("identity-bound config fault patch failed server dry-run")
    try:
        simulated = json.loads(dry_run.stdout)
        sim_resources = simulated["spec"]["template"]["spec"]["containers"][0].get("resources") or {}
        sim_meta = simulated.get("metadata", {}).get("annotations") or {}
        sim_pod_meta = simulated["spec"]["template"].get("metadata", {}).get("annotations") or {}
        if (simulated["metadata"]["uid"] != payload["uid"] or
                sim_resources != payload["injected_resources"] or
                sim_meta.get(LEASE_KEY) != payload["lease_id"] or
                sim_pod_meta.get(LEASE_KEY) != payload["lease_id"]):
            raise ValueError("server dry-run changed lease or resource fields")
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("server dry-run response could not be verified") from exc
    proc = subprocess.run(["kubectl", "patch", "deployment", name, "-n", namespace,
                           "--type=json", "-p", json.dumps(patch)], capture_output=True,
                          text=True, timeout=20)
    if proc.returncode:
        raise RuntimeError("identity-bound config fault patch failed")
    observed = kubectl_json("get", "deployment", name, "-n", namespace)
    annotations = observed.get("metadata", {}).get("annotations") or {}
    pod_annotations = observed["spec"]["template"].get("metadata", {}).get("annotations") or {}
    actual_resources = observed["spec"]["template"]["spec"]["containers"][0].get("resources") or {}
    if observed["metadata"]["uid"] != payload["uid"] or \
            annotations.get(LEASE_KEY) != payload["lease_id"] or \
            pod_annotations.get(LEASE_KEY) != payload["lease_id"] or \
            actual_resources != payload["injected_resources"]:
        # The API accepted a patch but did not produce our exact leased state.
        # Before returning an error, restore the resource fields synchronously
        # iff UID/resourceVersion/lease/current fields still match this read.
        if (observed["metadata"]["uid"] == payload["uid"] and
                annotations.get(LEASE_KEY) == payload["lease_id"] and
                pod_annotations.get(LEASE_KEY) == payload["lease_id"]):
            pointer = LEASE_KEY.replace("~", "~0").replace("/", "~1")
            repair = [
                {"op": "test", "path": "/metadata/uid", "value": payload["uid"]},
                {"op": "test", "path": "/metadata/resourceVersion",
                 "value": observed["metadata"]["resourceVersion"]},
                {"op": "test", "path": "/metadata/annotations/" + pointer,
                 "value": payload["lease_id"]},
                {"op": "test", "path": "/spec/template/metadata/annotations/" + pointer,
                 "value": payload["lease_id"]},
                {"op": "test", "path": "/spec/template/spec/containers/0/resources",
                 "value": actual_resources},
                {"op": "replace", "path": "/spec/template/spec/containers/0/resources",
                 "value": payload["original_resources"]},
                {"op": "remove", "path": "/metadata/annotations/" + pointer},
                {"op": "remove", "path": "/spec/template/metadata/annotations/" + pointer},
            ]
            rollback = subprocess.run(["kubectl", "patch", "deployment", name, "-n", namespace,
                                       "--type=json", "-p", json.dumps(repair)],
                                      capture_output=True, text=True, timeout=20)
            if rollback.returncode == 0:
                restored = kubectl_json("get", "deployment", name, "-n", namespace)
                restored_resources = restored["spec"]["template"]["spec"]["containers"][0].get("resources") or {}
                restored_meta = restored.get("metadata", {}).get("annotations") or {}
                restored_pod_meta = restored["spec"]["template"].get("metadata", {}).get("annotations") or {}
                if (restored["metadata"]["uid"] == payload["uid"] and
                        restored_resources == payload["original_resources"] and
                        LEASE_KEY not in restored_meta and LEASE_KEY not in restored_pod_meta):
                    raise RuntimeError("config fault patch mismatch; rollback verified")
        raise RuntimeError("config fault patch mismatch; rollback unverified; Job retained")
    return {"injected": True, "lease_id": payload["lease_id"],
            "deadline_epoch": payload["deadline_epoch"], "deployment_uid": payload["uid"]}


def cleanup_restored_lease(lease: dict) -> bool:
    """Remove scoped control objects only after exact restoration is observed."""
    payload = lease["payload"]
    live = kubectl_json("get", "deployment", payload["deployment"], "-n", payload["namespace"])
    annotations = live.get("metadata", {}).get("annotations") or {}
    pod_annotations = live.get("spec", {}).get("template", {}).get("metadata", {}).get("annotations") or {}
    actual = live["spec"]["template"]["spec"]["containers"][0].get("resources") or {}
    if live["metadata"]["uid"] != payload["uid"] or actual != payload["original_resources"] or \
            LEASE_KEY in annotations or LEASE_KEY in pod_annotations:
        return False
    name = lease["job_name"]
    for namespace, resources in ((CONTROL_NAMESPACE, ["job", "configmap", "serviceaccount"]),
                                 (payload["namespace"], ["rolebinding", "role"])):
        for resource in resources:
            proc = subprocess.run(["kubectl", "delete", resource, name, "-n", namespace,
                                   "--ignore-not-found=true", "--wait=true", "--timeout=30s"],
                                  capture_output=True, text=True, timeout=40)
            if proc.returncode:
                return False
    return True


def cleanup_terminal_no_mutation(lease: dict) -> bool:
    """Drop scoped RBAC after an audited permanent identity mismatch."""
    name = lease["job_name"]
    job = kubectl_json("get", "job", name, "-n", CONTROL_NAMESPACE)
    if job.get("status", {}).get("succeeded", 0) < 1:
        return False
    logs = subprocess.run(["kubectl", "logs", "-n", CONTROL_NAMESPACE,
                           f"job/{name}", "--tail=20"], capture_output=True,
                          text=True, timeout=15)
    if logs.returncode or '"status": "terminal_no_mutation"' not in logs.stdout:
        return False
    payload = lease["payload"]
    live = kubectl_json("get", "deployment", payload["deployment"], "-n", payload["namespace"])
    if live.get("metadata", {}).get("uid") != payload["uid"] or \
            (live["spec"]["template"]["spec"]["containers"][0].get("resources") or {}) != payload["original_resources"] or \
            LEASE_KEY in (live.get("metadata", {}).get("annotations") or {}) or \
            LEASE_KEY in (live["spec"]["template"].get("metadata", {}).get("annotations") or {}):
        return False
    return not cleanup_unarmed(name, lease["payload"]["namespace"])
