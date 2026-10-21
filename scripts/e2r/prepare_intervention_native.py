#!/usr/bin/env python3
"""Prepare isolated native service slices; application code is not replaced by a simulator."""
from __future__ import annotations

import argparse
import copy
import json
import secrets
import subprocess
import time
from pathlib import Path

BASE = ["kubectl", "--kubeconfig", "/etc/rancher/k3s/k3s.yaml", "--request-timeout=20s"]
OWNER = "e2r-intervention-native-20260929"


def kube(*args, payload=None, check=True):
    result = subprocess.run([*BASE, *args], input=json.dumps(payload) if payload is not None else None,
                            capture_output=True, text=True, timeout=45)
    if check and result.returncode:
        raise RuntimeError(result.stderr[-1500:])
    return result


def get(kind, name, namespace):
    return json.loads(kube("get", kind, name, "-n", namespace, "-o", "json").stdout)


def service(ns, name, app, port, target=None):
    return {"apiVersion": "v1", "kind": "Service", "metadata": {"name": name, "namespace": ns,
            "labels": {"e2r-owner": OWNER}}, "spec": {"selector": {"app": app},
            "ports": [{"name": "api", "port": port, "targetPort": target or port}]}}


def deployment(ns, name, image, port, env, *, cpu="300m", memory="512Mi", replicas=1, args=None):
    container = {"name": "app", "image": image, "imagePullPolicy": "IfNotPresent",
                 "ports": [{"containerPort": port}], "env": [{"name": k, "value": str(v)} for k,v in env.items()],
                 "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"cpu": cpu, "memory": memory}},
                 "readinessProbe": {"tcpSocket": {"port": port}, "periodSeconds": 2, "timeoutSeconds": 1,
                                    "failureThreshold": 3},
                 "startupProbe": {"tcpSocket": {"port": port}, "periodSeconds": 3, "failureThreshold": 80}}
    if args is not None:
        container["args"] = args
    return {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": name, "namespace": ns,
            "labels": {"e2r-owner": OWNER}}, "spec": {"replicas": replicas, "revisionHistoryLimit": 2,
            "strategy": {"type": "Recreate"}, "selector": {"matchLabels": {"app": name}},
            "template": {"metadata": {"labels": {"app": name, "e2r-owner": OWNER}},
                         "spec": {"terminationGracePeriodSeconds": 5, "containers": [container]}}}}


def base_objects(ns):
    return [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ns,
             "labels": {"e2r-owner": OWNER, "e2r-isolated-experiment": "true"}}},
            {"apiVersion": "v1", "kind": "ResourceQuota", "metadata": {"name": "experiment-budget", "namespace": ns},
             "spec": {"hard": {"requests.cpu": "6", "limits.cpu": "8", "requests.memory": "6Gi",
                                "limits.memory": "8Gi", "pods": "24"}}}]


def prepare(root):
    root.mkdir(parents=True, exist_ok=False)
    native = {}
    ns = "e2r-isg-boutique"
    cart = get("deployment", "cartservice", "online-boutique")["spec"]["template"]["spec"]["containers"][0]
    frontend = get("deployment", "frontend", "online-boutique")["spec"]["template"]["spec"]["containers"][0]
    redis = get("deployment", "redis-cart", "online-boutique")["spec"]["template"]["spec"]["containers"][0]
    objs = base_objects(ns)
    objs += [deployment(ns, "shared-db", redis["image"], 6379, {}, cpu="200m", memory="256Mi",
                        args=["redis-server", "--maxclients", "128", "--save", "", "--appendonly", "no"]),
             service(ns, "shared-db", "shared-db", 6379)]
    for role in ("old", "new", "peer"):
        name = "cart-"+role
        objs += [deployment(ns, name, cart["image"], 7070, {"REDIS_ADDR": "shared-db:6379", "ENABLE_TRACING": "false"}),
                 service(ns, name, name, 7070)]
    objs.append(service(ns, "target", "cart-new", 7070))
    for role, cart_target in (("business", "target"), ("old-probe", "cart-old"), ("new-probe", "cart-new"), ("peer", "cart-peer")):
        env = {e["name"]: e["value"] for e in frontend.get("env", []) if "value" in e}
        for key, value in list(env.items()):
            if key.endswith("_SERVICE_ADDR") and ":" in value:
                host, port = value.rsplit(":", 1)
                env[key] = host + ".online-boutique.svc.cluster.local:" + port
        env.update(CART_SERVICE_ADDR=cart_target+":7070", ENABLE_TRACING="false", DISABLE_PROFILER="true", PORT="8080",
                   CHECKOUT_SERVICE_ADDR="disabled-checkout:1", SHOPPING_ASSISTANT_SERVICE_ADDR="disabled-assistant:1")
        name = "frontend-"+role
        objs += [deployment(ns, name, frontend["image"], 8080, env, cpu="300m", memory="256Mi"),
                 service(ns, name, name, 8080)]
    native["boutique"] = {"namespace": ns, "objects": objs, "application": "Online Boutique native cart/Redis dependency slice",
                           "source_images": [cart["image"], frontend["image"], redis["image"]]}

    ns = "e2r-isg-trainticket"
    station = get("deployment", "ts-station-service", "train-ticket")["spec"]["template"]["spec"]["containers"][0]
    root_password, app_password = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "database-credentials", "namespace": ns},
              "type": "Opaque", "stringData": {"root_password": root_password, "app_password": app_password}}
    private = root / "private"
    private.mkdir(mode=0o700)
    with (private/"mysql-secret.json").open("x") as f:
        json.dump(secret, f)
    (private/"mysql-secret.json").chmod(0o600)
    objs = base_objects(ns)
    db = deployment(ns, "shared-db", "docker.m.daocloud.io/library/mysql:5.7", 3306,
                    {"MYSQL_DATABASE": "ts", "MYSQL_USER": "station"}, cpu="500m", memory="768Mi",
                    args=["--max-connections=128", "--innodb-buffer-pool-size=128M", "--skip-name-resolve"])
    db["spec"]["template"]["spec"]["containers"][0]["env"] += [
        {"name": "MYSQL_ROOT_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "database-credentials", "key": "root_password"}}},
        {"name": "MYSQL_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "database-credentials", "key": "app_password"}}}]
    db["spec"]["template"]["spec"]["volumes"] = [{"name": "database", "emptyDir": {}}]
    db["spec"]["template"]["spec"]["containers"][0]["volumeMounts"] = [{"name": "database", "mountPath": "/var/lib/mysql"}]
    objs += [db, service(ns, "shared-db", "shared-db", 3306)]
    for role in ("new", "old", "peer"):
        name = "station-"+role
        env = {"STATION_MYSQL_HOST": "shared-db", "STATION_MYSQL_PORT": "3306", "STATION_MYSQL_DATABASE": "ts",
               "STATION_MYSQL_USER": "station", "SPRING_CLOUD_NACOS_DISCOVERY_ENABLED": "false",
               "SPRING_CLOUD_NACOS_DISCOVERY_REGISTER_ENABLED": "false", "SPRING_CLOUD_DISCOVERY_ENABLED": "false",
               "SPRING_DATASOURCE_HIKARI_MINIMUM_IDLE": "1", "SPRING_DATASOURCE_HIKARI_MAXIMUM_POOL_SIZE": "10",
               "JAVA_TOOL_OPTIONS": "-Xms128m -Xmx384m -XX:ParallelGCThreads=2 -XX:ConcGCThreads=1 -XX:CICompilerCount=2 -Djava.util.concurrent.ForkJoinPool.common.parallelism=2 -Dio.netty.eventLoopThreads=2"}
        obj = deployment(ns, name, station["image"], 12345, env, cpu="1000m", memory="768Mi", replicas=1 if role=="new" else 0)
        obj["spec"]["template"]["spec"]["containers"][0]["env"].append(
            {"name": "STATION_MYSQL_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "database-credentials", "key": "app_password"}}})
        objs += [obj, service(ns, name, name, 12345)]
    objs.append(service(ns, "target", "station-new", 12345))
    native["trainticket"] = {"namespace": ns, "objects": objs, "application": "TrainTicket native station/MySQL dependency slice",
                             "source_images": [station["image"], db["spec"]["template"]["spec"]["containers"][0]["image"]]}
    for name, value in native.items():
        (root/(name+".json")).write_text(json.dumps(value, indent=2)+"\n")
    (root/"README.md").write_text("Native application images; isolated namespaces; no application responses or action effects are simulated. TrainTicket discovery is disabled and the database is private to this slice. The station image initializes its public demo station data. Cart frontends use isolated carts/Redis and read-only original catalog dependencies. These are dependency-slice studies, not full-application recovery claims.\n")
    print(json.dumps({"prepared": str(root), "namespaces": [v["namespace"] for v in native.values()]}))


def apply(root):
    for name in ("boutique", "trainticket"):
        value = json.loads((root/(name+".json")).read_text())
        ns = value["namespace"]
        found = kube("get", "namespace", ns, "-o", "json", check=False)
        if found.returncode == 0 and json.loads(found.stdout)["metadata"].get("labels", {}).get("e2r-owner") != OWNER:
            raise ValueError("refuse to modify a namespace not owned by this experiment")
        kube("apply", "-f", "-", payload=value["objects"][0])
        if name == "trainticket":
            kube("apply", "-f", str(root/"private/mysql-secret.json"))
        for obj in value["objects"][1:]:
            kube("apply", "-f", "-", payload=obj)
        print(json.dumps({"namespace": ns, "state": "baseline_resources_applied"}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "apply"])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    (prepare if args.command == "prepare" else apply)(args.out)


if __name__ == "__main__":
    main()
