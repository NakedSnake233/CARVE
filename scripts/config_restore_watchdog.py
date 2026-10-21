#!/usr/bin/env python3
"""Independent deadline restore for a single injected Deployment config fault.

The collector launches this process in a new session before patching a resource
limit. If the collector is killed, the watchdog reapplies that Deployment's
frozen manifest after the lease expires. It acts only while the injected tiny
CPU limit is still present.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

import yaml


SAFE_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


def original_deployment(manifest: Path, name: str) -> dict:
    for doc in yaml.safe_load_all(manifest.read_text()):
        if isinstance(doc, dict) and doc.get("kind") == "Deployment" and \
                (doc.get("metadata") or {}).get("name") == name:
            return doc
    raise ValueError(f"Deployment {name} absent from original manifest")


def cpu_millicores(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    try:
        return float(text[:-1]) if text.endswith("m") else float(text) * 1000.0
    except ValueError:
        return None


def injected_limit_present(deployment: dict) -> bool:
    containers = (((deployment.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []
    if not containers:
        return False
    limits = (containers[0].get("resources") or {}).get("limits") or {}
    cpu = cpu_millicores(limits.get("cpu"))
    return cpu is not None and cpu <= 5


def kubectl_get(name: str, namespace: str) -> dict:
    proc = subprocess.run(["kubectl", "get", "deployment", name, "-n", namespace,
                           "-o", "json"], capture_output=True, text=True, timeout=20,
                          check=True)
    return json.loads(proc.stdout)


def run(namespace: str, name: str, manifest: Path, deadline_epoch: float) -> dict:
    if namespace not in {"train-ticket", "online-boutique"} or not SAFE_NAME.fullmatch(name):
        raise ValueError("watchdog namespace/deployment outside benchmark scope")
    desired = original_deployment(manifest, name)
    while True:
        remaining = deadline_epoch - time.time()
        if remaining <= 0:
            break
        time.sleep(min(5.0, remaining))
    live = kubectl_get(name, namespace)
    if not injected_limit_present(live):
        return {"status": "already_restored", "namespace": namespace, "deployment": name}
    desired.setdefault("metadata", {})["namespace"] = namespace
    proc = subprocess.run(["kubectl", "apply", "-f", "-"], input=yaml.safe_dump(desired),
                          capture_output=True, text=True, timeout=30)
    if proc.returncode:
        return {"status": "restore_failed", "namespace": namespace, "deployment": name,
                "error_type": "KubectlApplyFailed"}
    # Verify the mutation, not only kubectl's exit status.
    current = kubectl_get(name, namespace)
    return {"status": "restored" if not injected_limit_present(current) else "restore_unverified",
            "namespace": namespace, "deployment": name}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--namespace", required=True)
    ap.add_argument("--deployment", required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--deadline-epoch", type=float, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    os.environ["KUBECONFIG"] = "/etc/rancher/k3s/k3s.yaml"
    try:
        result = run(args.namespace, args.deployment, args.manifest, args.deadline_epoch)
    except Exception as exc:
        result = {"status": "watchdog_error", "namespace": args.namespace,
                  "deployment": args.deployment, "error_type": type(exc).__name__}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    return 0 if result["status"] in {"already_restored", "restored"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
