"""Observed state and transactions for isolated native application dependency slices."""
from __future__ import annotations
from pathlib import Path

import copy
import datetime
import json
import math
import subprocess
import time
from dataclasses import asdict

from .campaign import KubernetesBackend, collector, http_request
from .platform import LiveProvider, graph_observation
from .schema import Fact, Relation, EvidenceGraph, GraphObservation
from .native_resources import cpu_cores, memory_bytes, scheduler_slots
from .redis_probe import read_command as read_redis_probe


class NativeProvider(LiveProvider):
    """Probe answers are measurements, never a scenario label or predicted outcome."""
    def __init__(self, inventory, client, study):
        super().__init__(inventory, client)
        self.study = study
        self.references = {}
        self.raw_probes = []

    def _fact(self, graph, entity, variable, value, source, observed_at=None, ttl=120, origin="observed"):
        stamp = time.time() if observed_at is None else observed_at
        return Fact(f"native:{entity}:{variable}:{stamp}", entity, variable, value, stamp, ttl, source,origin=origin,
                    uid=graph.entity(entity).uid)

    def native_roles(self):
        return self.study.config.get('native_roles',['old','new','peer'])

    def configured_database(self,template):
        env={e['name']:e.get('value','') for c in template['spec']['containers'] for e in c.get('env',[])}
        binding=env.get('REDIS_ADDR',env.get('STATION_MYSQL_HOST',env.get('SPRING_DATASOURCE_URL','')))
        return (binding.split(':',1)[0].split('.',1)[0] if self.study.config['native_application']=='boutique'
                else binding.split('//',1)[-1].split('/',1)[0].split(':',1)[0].split('.',1)[0])

    def collect(self):
        graph = super().collect()
        now = time.time()
        objects = {e.id: self.client.get(e.kind, e.name) for e in graph.entities if e.kind in ("Deployment", "Service")}
        quota = self.client._call(["get", "resourcequota", "experiment-budget", "-o", "json"])
        deployments = self.client.list("Deployment")
        pods = self.client.list("Pod")
        cluster_pods = self.client._call_cluster(["get","pods","--all-namespaces","-o","json"])["items"]
        nodes = self.client._call_cluster(["get","nodes","-o","json"])["items"]
        committed = sum(d["spec"].get("replicas", 1)*sum(cpu_cores(c["resources"]["limits"]["cpu"])
                        for c in d["spec"]["template"]["spec"]["containers"]) for d in deployments)
        remaining = max(0.0, cpu_cores(quota["spec"]["hard"]["limits.cpu"])-committed)
        for entity in graph.entities:
            if entity.id=='background':
                workload=self.study.workloads.get('background')
                declaration=getattr(self.study,'active_background_workloads',self.study.config.get('background_workloads',{})).get('background',{})
                rate=workload.offered_rps if workload else declaration.get('offered_rps',0)
                size=workload.spec.get('payload_bytes',0) if workload else declaration.get('payload_bytes',0)
                for variable,value in {'request_rate':rate,'payload_bytes':size,'offered_bytes_per_second':rate*size*2}.items():
                    graph.facts.append(self._fact(graph,entity.id,variable,value,'registered_native_SET_GET_background_demand',origin='configuration'))
            if entity.role == "shared_dependency" and entity.kind == "Deployment":
                limit = sum(cpu_cores(c["resources"]["limits"]["cpu"]) for c in objects[entity.id]["spec"]["template"]["spec"]["containers"])
                graph.facts.append(self._fact(graph,entity.id,"cpu_limit",limit,"kubernetes_container_cpu_limits"))
            if entity.id in self.native_roles():
                obj = objects[entity.id]
                unit = sum(cpu_cores(c["resources"]["limits"]["cpu"]) for c in obj["spec"]["template"]["spec"]["containers"])
                slots = scheduler_slots(nodes,cluster_pods,self.client.namespace,quota,obj["spec"]["template"],committed)
                graph.facts += [self._fact(graph, entity.id, "scheduler_headroom", slots, "observed_single_node_pod_request_and_namespace_quota_budgets"),
                                self._fact(graph, entity.id, "cpu_limit", unit, "kubernetes_container_cpu_limits"),
                                self._fact(graph, entity.id, "load", self.study.workloads["peer"].offered_rps if entity.id=="peer" else self.study.offered_rps, "registered_paced_workload",origin='configuration'),
                                self._fact(graph, entity.id, "dependencies_known", None if self.study.config.get("discover_dependencies",False) else True, "registered_native_dependency_inventory",origin='contract')]
                starts = [p.get("status",{}).get("startTime") for p in pods
                          if p["metadata"].get("labels",{}).get("app")==entity.name and not p["metadata"].get("deletionTimestamp")]
                ages = [max(0,now-datetime.datetime.fromisoformat(stamp.replace("Z","+00:00")).timestamp()) for stamp in starts if stamp]
                graph.facts.append(self._fact(graph,entity.id,"warmup_age_seconds",min(300,min(ages)) if ages else None,
                    "minimum_pod_uptime_capped_at_300_seconds_not_a_proof_of_request_warmup"))
                if entity.id=='peer' and self.study.baselines.get('peer'):
                    baseline=self.study.baselines['peer']
                    ceiling=max(baseline.p95_ms*self.study.protocol.latency_multiplier,self.study.protocol.latency_floor_ms)
                    graph.facts.append(self._fact(graph,'peer','latency_budget_ms',ceiling,
                        'registered_verification_protocol_and_healthy_non_target_baseline',origin='configuration'))
            if entity.id == "entry":
                selector = objects[entity.id]["spec"].get("selector", {})
                matched = False
                for target in self.native_roles():
                    labels = objects[target]["spec"]["template"]["metadata"]["labels"]
                    present = bool(selector) and all(labels.get(k) == v for k,v in selector.items())
                    graph.relations = [r for r in graph.relations if not (r.source=="entry" and r.target==target and r.kind=="routes_to")]
                    graph.relations.append(Relation("entry", target, "routes_to", now, 120, "observed", (), "present" if present else "absent"))
                    matched |= present
                graph.facts += [self._fact(graph, "entry", "selector_matches", matched, "service_selector_vs_deployment_labels"),
                                self._fact(graph, "entry", "dependencies_known", True, "native_routing_adapter")]
                baseline = self.study.baselines.get("business")
                if baseline:
                    ceiling = max(baseline.p95_ms*self.study.protocol.latency_multiplier,self.study.protocol.latency_floor_ms)
                    graph.facts.append(self._fact(graph,"entry","latency_budget_ms",ceiling,"registered_verification_protocol_and_healthy_baseline",origin='configuration'))
                fault = getattr(self.study,"fault_window",None)
                if fault:
                    for variable,value in {"error_rate":fault.error_rate,"latency_ms":fault.p95_ms,
                                           "request_rate":fault.throughput_rps}.items():
                        graph.facts.append(self._fact(graph,"entry",variable,value,"measured_pre_action_transaction_window",fault.measurement_completed_at or fault.end_time))
        databases={e.name:e.id for e in graph.entities if e.role=='shared_dependency' and e.kind=='Deployment'}
        if self.study.config.get('observe_configuration_bindings',False):
            reachable={};service_receipts={}
            for name,identity in databases.items():
                service=self.client.get('Service',name);selector=service['spec'].get('selector',{})
                labels=objects[identity]['spec']['template']['metadata'].get('labels',{})
                reachable[name]=bool(selector) and all(labels.get(k)==v for k,v in selector.items())
                service_receipts[name]={'uid':service.get('metadata',{}).get('uid'),
                    'resource_version':service.get('metadata',{}).get('resourceVersion'),'selector_matches':reachable[name]}
            for role in self.native_roles():
                host=self.configured_database(objects[role]['spec']['template']);valid=host in databases and reachable.get(host,False)
                graph.facts.append(self._fact(graph,role,'dependencies_known',True if valid else None,
                    'observed_native_configuration_and_registered_service_selector',origin='configuration'))
                graph.relations=[r for r in graph.relations if not (r.source==role and r.kind=='competes_for')]
                for name,identity in databases.items():
                    graph.relations.append(Relation(role,identity,'competes_for',now,120,'configuration',(),
                        'present' if valid and host==name else 'absent' if valid else 'unknown'))
                if getattr(self.study,'current_dir',None):
                    from .campaign import append_json
                    append_json(self.study.current_dir/'configuration_binding_observations.jsonl',{
                        'role':role,'deployment_name':graph.entity(role).name,'deployment_uid':objects[role]['metadata']['uid'],
                        'deployment_resource_version':objects[role]['metadata'].get('resourceVersion'),
                        'configured_database':host,'registered_service':service_receipts.get(host),
                        'known':bool(valid),'observed_at':time.time(),'evidence_kind':'configuration_not_effect_prediction'})
        for label,name in getattr(self.study,'active_background_bindings',{}).items():
            graph.relations=[r for r in graph.relations if not (r.source==label and r.kind=='competes_for')]
            graph.relations.append(Relation(label,databases[name],'competes_for',now,120,'configuration'))
        if self.references:
            ref = self.references
            values = {"healthy": ref["healthy"], "capacity": ref["capacity"], "compatible": True, "version_approved": True}
            if "latency_ms" in ref:
                values["latency_ms"] = ref["latency_ms"]
            for key, value in values.items():
                contract=key in ('compatible','version_approved')
                graph.facts.append(self._fact(graph, "approved_new", key, value,
                    "study_approved_same_image_template_schema_contract" if contract else "verified_pre_fault_version_reference",
                    ref["observed_at"], ttl=600,origin='contract' if contract else 'observed'))
            graph.facts.append(self._fact(graph, "approved_new", "load", self.study.offered_rps, "registered_paced_workload",origin='configuration'))
        graph.at = time.time()
        graph.__post_init__()
        return graph

    def _database_read(self, command, budget):
        """Retry one timed-out read per probe, within its existing total deadline."""
        while True:
            remaining = budget["deadline"] - time.perf_counter()
            if remaining <= 0:
                raise TimeoutError("native database probe exhausted its declared budget")
            timeout = min(10.0, remaining)
            began = time.perf_counter()
            attempt = {"command": command, "started_at": time.time(), "timeout_seconds": timeout}
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=True)
                attempt["status"] = "completed"
            except subprocess.TimeoutExpired:
                attempt.update(status="timeout", error="TimeoutExpired")
                if budget["retries_remaining"] and time.perf_counter() < budget["deadline"]:
                    budget["retries_remaining"] -= 1
                    continue
                raise
            except subprocess.CalledProcessError as error:
                attempt.update(status="failed", error="CalledProcessError", exit_code=error.returncode)
                raise
            finally:
                attempt["seconds"] = time.perf_counter() - began
                budget["attempts"].append(attempt)
            if time.perf_counter() >= budget["deadline"]:
                raise TimeoutError("native database probe exhausted its declared budget")
            return result.stdout

    def _redis_database_read(self, address, command, budget):
        while True:
            remaining = budget["deadline"] - time.perf_counter()
            if remaining <= 0:
                raise TimeoutError("native database probe exhausted its declared budget")
            began = time.perf_counter()
            timeout = min(10.0, remaining)
            attempt = {"transport":"redis_service_resp", "address":list(address), "command":command,
                       "started_at":time.time(), "timeout_seconds":timeout}
            try:
                result = read_redis_probe(address, command, deadline=min(budget["deadline"], began+10.0))
                attempt["status"] = "completed"
            except TimeoutError:
                attempt.update(status="timeout", error="TimeoutError")
                if budget["retries_remaining"] and time.perf_counter() < budget["deadline"]:
                    budget["retries_remaining"] -= 1
                    continue
                raise
            except Exception as error:
                attempt.update(status="failed", error=type(error).__name__, detail=str(error))
                raise
            finally:
                attempt["seconds"] = time.perf_counter()-began
                budget["attempts"].append(attempt)
            if time.perf_counter() >= budget["deadline"]:
                raise TimeoutError("native database probe exhausted its declared budget")
            return result

    def _database(self, deployment, budget):
        namespace = self.client.namespace
        if self.study.config["native_application"] == "boutique":
            endpoints = {}
            for kind in ("Service", "Deployment"):
                if time.perf_counter() >= budget["deadline"]:
                    raise TimeoutError("native database probe exhausted its declared budget")
                endpoints[kind] = self.client.get(kind, deployment)
                if time.perf_counter() >= budget["deadline"]:
                    raise TimeoutError("native database probe exhausted its declared budget")
            service, target = endpoints["Service"], endpoints["Deployment"]
            selector = service["spec"].get("selector", {})
            labels = target["spec"]["template"]["metadata"]["labels"]
            ports = [p for p in service["spec"].get("ports", [])
                     if p.get("protocol", "TCP") == "TCP" and p.get("targetPort", p["port"]) == 6379]
            address = service["spec"].get("clusterIP")
            if (not address or address == "None" or len(ports) != 1 or not selector or
                    any(labels.get(k) != v for k, v in selector.items())):
                raise ValueError("Redis Service does not select its registered database deployment")
            commands = [["INFO", "clients"], ["CONFIG", "GET", "maxclients"], ["CLIENT", "LIST"]]
            outputs = [self._redis_database_read((address, ports[0]["port"]), command, budget) for command in commands]
            info = dict(line.split(":", 1) for line in outputs[0].splitlines() if ":" in line and not line.startswith("#"))
            if not isinstance(outputs[1], list) or len(outputs[1]) != 2 or outputs[1][0] != "maxclients":
                raise ValueError("invalid Redis configuration observation")
            limit = int(outputs[1][1])
            clients = [dict(part.split("=",1) for part in line.split() if "=" in part) for line in outputs[2].splitlines()]
            by_ip = {}
            for row in clients:
                ip = row.get("addr", "").rsplit(":",1)[0]
                by_ip[ip] = by_ip.get(ip,0)+1
            return {"limit": limit, "connected": int(info["connected_clients"]), "clients_by_ip": by_ip,
                    "probe_transport":"redis_service_resp", "service_uid":service["metadata"]["uid"],
                    "service_resource_version":service["metadata"].get("resourceVersion"),
                    "service_address":[address,ports[0]["port"]]}
        query = "SHOW VARIABLES LIKE 'max_connections'; SHOW GLOBAL STATUS LIKE 'Threads_connected'; SELECT SUBSTRING_INDEX(HOST,':',1),COUNT(*) FROM information_schema.PROCESSLIST WHERE USER='station' GROUP BY SUBSTRING_INDEX(HOST,':',1);"
        command = ["kubectl", "--kubeconfig", self.client.context.kubeconfig, "-n", namespace,
                   "exec", "deployment/"+deployment, "--", "sh", "-c",
                   'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -N -e "$1"', "sh", query]
        output = self._database_read(command, budget)
        lines = [line.split("\t") for line in output.splitlines()]
        values = dict(lines[:2])
        return {"limit": int(values["max_connections"]), "connected": int(values["Threads_connected"]),
                "clients_by_ip": {ip:int(n) for ip,n in lines[2:]}}

    def _metrics_read(self, args, deadline, attempts):
        """Retry one transient metrics-service outage without refreshing metric timestamps."""
        for index in range(2):
            if time.perf_counter() >= deadline:
                raise TimeoutError("native metrics probe exhausted its declared budget")
            began = time.perf_counter()
            attempt = {"request": args, "started_at": time.time()}
            try:
                value = self.client._call(args)
            except Exception as error:
                attempt.update(status="failed", error=type(error).__name__, detail=str(error),
                               seconds=time.perf_counter()-began)
                attempts.append(attempt)
                transient = (isinstance(error, RuntimeError) and
                             "Error from server (ServiceUnavailable)" in str(error))
                if index == 0 and transient and deadline-time.perf_counter() > .2:
                    attempt["retry_backoff_seconds"] = .2
                    time.sleep(.2)
                    continue
                raise
            attempt.update(status="completed", seconds=time.perf_counter()-began)
            attempts.append(attempt)
            if time.perf_counter() >= deadline:
                raise TimeoutError("native metrics probe exhausted its declared budget")
            return value

    def probe(self, spec, graph):
        if spec not in self.probes:
            raise ValueError("unregistered native observation")
        started = time.perf_counter()
        metrics_attempts = None
        if spec.id == "shared_dependency":
            databases = {e.name:e.id for e in graph.entities if e.role=="shared_dependency" and e.kind=="Deployment"}
            budget = {"deadline": started + spec.timeout, "retries_remaining": 1, "attempts": []}
            try:
                measured = {name:self._database(name, budget) for name in databases}
                pods = self.client.list("Pod")
                facts = [{"entity":databases[name], "variable":"connection_headroom", "value":max(0,db["limit"]-db["connected"])}
                         for name,db in measured.items()]
                relations = []
                for role in self.native_roles():
                    name = graph.entity(role).name
                    deployment = self.client.get("Deployment",name)
                    host=self.configured_database(deployment['spec']['template'])
                    if host not in measured:
                        raise ValueError("native dependency binding does not name a registered study database")
                    db=measured[host]
                    ips = [p.get("status",{}).get("podIP") for p in pods if p["metadata"].get("labels",{}).get("app")==name]
                    # Current observed connections per replica, not an asserted causal maximum.
                    value = sum(db["clients_by_ip"].get(ip,0) for ip in ips)/len(ips) if ips else None
                    facts.append({"entity":role, "variable":"connections_per_replica", "value":value})
                    facts.append({"entity":role,"variable":"dependencies_known","value":True})
                    relations += [{"source":role,"target":identity,"kind":"competes_for",
                                   "validity":"present" if target==host else "absent"} for target,identity in databases.items()]
                payload = {"facts":facts,"relations":relations}
                result = graph_observation(payload, spec, graph, time.time(), self.client.namespace)
                raw = measured
            except Exception as error:
                self.raw_probes.append({"probe_id":spec.id, "measured_at":time.time(),
                    "seconds":time.perf_counter()-started, "status":"failed", "error":type(error).__name__,
                    "detail":str(error), "database_read_attempts":budget["attempts"]})
                raise
        elif spec.variable == "healthy":
            value, latency = http_request(self.study.direct_workloads[spec.entity]["canary"])
            result = self._fact(graph, spec.entity, "healthy", value, "native_business_canary")
            raw = {"ok":value,"milliseconds":latency}
        elif spec.variable == "capacity":
            definition = copy.deepcopy(self.study.direct_workloads[spec.entity])
            definition.update(duration=4.0, offered_rps=max(5.0,self.study.offered_rps*2), workers=48)
            window, raw = collector(definition).collect(f"capacity-{spec.entity}-{time.time()}")
            value = window.throughput_rps if window.state_canary_ok is True else None
            if spec.kind == "native_graph":
                result = graph_observation({"facts":[{"entity":spec.entity,"variable":"capacity","value":value},
                    {"entity":spec.entity,"variable":"latency_ms","value":window.p95_ms},
                    {"entity":spec.entity,"variable":"error_rate","value":window.error_rate},
                    {"entity":spec.entity,"variable":"healthy","value":window.state_canary_ok}]},
                    spec,graph,time.time(),self.client.namespace)
            else:
                result = self._fact(graph, spec.entity, "capacity", value, "measured_bounded_probe_successful_transactions_per_second")
            raw["window"] = asdict(window)
        else:
            # Preserve the metrics API's actual observation time and exclude
            # metrics for replaced/absent pods; querying a cache is not freshness.
            from urllib.parse import quote
            selector="app="+graph.entity(spec.entity).name
            metrics_attempts = []
            try:
                metrics=self._metrics_read(["get","--raw",f"/apis/metrics.k8s.io/v1beta1/namespaces/{self.client.namespace}/pods?labelSelector={quote(selector,safe='')}"],
                                           started+spec.timeout,metrics_attempts)["items"]
                current={p["metadata"]["name"]:p for p in self.client.list("Pod",selector) if not p["metadata"].get("deletionTimestamp")}
                selected=[row for row in metrics if row["metadata"]["name"] in current]
                stamps=[datetime.datetime.fromisoformat(row["timestamp"].replace("Z","+00:00")).timestamp() for row in selected]
                valid=bool(current) and {r["metadata"]["name"] for r in selected}==set(current) and all(stamp<=time.time() for stamp in stamps)
                valid &= all(datetime.datetime.fromisoformat(current[row["metadata"]["name"]]["status"]["startTime"].replace("Z","+00:00")).timestamp()<=stamp
                             for row,stamp in zip(selected,stamps))
                parse=cpu_cores if spec.variable=="cpu" else lambda v:memory_bytes(v)/1024**2
                resource="cpu" if spec.variable=="cpu" else "memory"
                value=sum(parse(c["usage"][resource]) for row in selected for c in row["containers"]) if valid else None
                result = self._fact(graph,spec.entity,spec.variable,value,"metrics.k8s.io/pods",min(stamps) if valid else time.time(),ttl=spec.ttl)
                raw = {"metrics":selected,"current_pod_uids":{name:p["metadata"]["uid"] for name,p in current.items()},"complete_current_pod_coverage":bool(valid)}
            except Exception as error:
                self.raw_probes.append({"probe_id":spec.id, "measured_at":time.time(),
                    "seconds":time.perf_counter()-started, "status":"failed", "error":type(error).__name__,
                    "detail":str(error), "metrics_read_attempts":metrics_attempts})
                raise
        elapsed = time.perf_counter()-started
        self.raw_probes.append({"probe_id":spec.id,"measured_at":time.time(),"seconds":elapsed,"raw":raw})
        if spec.id == "shared_dependency":
            self.raw_probes[-1]["database_read_attempts"] = budget["attempts"]
        if metrics_attempts is not None:
            self.raw_probes[-1]["metrics_read_attempts"] = metrics_attempts
        if elapsed > spec.timeout:
            self.raw_probes[-1].update(status="failed", error="TimeoutError",
                                      detail="native probe exceeded its declared budget")
            raise TimeoutError("native probe exceeded its declared budget")
        return result


class NativeBackend(KubernetesBackend):
    """Study lifecycle using real application containers and independently measured transactions."""
    def __init__(self, config, out_dir):
        super().__init__(config,out_dir)
        expected = {"boutique":"e2r-isg-boutique","trainticket":"e2r-isg-trainticket"}
        base=expected.get(config.get("native_application"))
        allowed={base,*[str(base)+'-p'+str(n) for n in (2,3,4)]} if base else set()
        if self.client.namespace not in allowed:
            raise ValueError("native study writes are limited to its declared experiment namespaces")
        if self.client.namespace != base:
            labels=self.client._call(['get','namespace',self.client.namespace,'-o','json'])['metadata'].get('labels',{})
            if labels.get('e2r-owner')!='e2r-intervention-native-20260929' or labels.get('e2r-cloned-from')!=base:
                raise ValueError('replica namespace ownership/source has not been verified')
        self.provider = NativeProvider(config["inventory"], self.client, self)
        self.direct_workloads = config["direct_workloads"]
        self.offered_rps = config["workloads"]["business"]["offered_rps"]
        self.case = None
        roles=config.get('native_roles',['old','new','peer'])
        inventory_roles={e['id'] for e in config['inventory']['entities'] if e.get('kind')=='Deployment'}
        if (len(roles)!=len(set(roles)) or not {'old','new','peer'}<=set(roles)
                or not set(roles)<={'old','new','peer','alternative'} or not set(roles)<=set(self.direct_workloads)
                or not set(roles)<=inventory_roles):
            raise ValueError('native role inventory or direct workloads are inconsistent')

    def reset_application_state(self, directory, phase):
        return self.timed_call('application_state_reset_'+phase,self._reset_application_state,directory,phase)

    def _reset_application_state(self, directory, phase):
        namespace = self.client.namespace
        owner = self.client._call(["get","namespace",namespace,"-o","json"])["metadata"].get("labels",{}).get("e2r-owner")
        if owner != "e2r-intervention-native-20260929":
            raise RuntimeError("refuse application data reset outside this study's owned namespace")
        if self.config["native_application"] != "boutique":
            return
        if self.traffic is not None:
            raise RuntimeError("cart state cannot reset until all workload threads have stopped")
        databases=[e["name"] for e in self.config["inventory"]["entities"] if e["role"]=="shared_dependency" and e.get("kind")=="Deployment"]
        if not set(databases) <= {"shared-db","private-db","reference-db"}:
            raise ValueError("unregistered native Redis reset target")
        counts={}
        for name in databases:
            base=["kubectl","--kubeconfig",self.client.context.kubeconfig,"-n",namespace,
                  "exec","deployment/"+name,"--","redis-cli","--raw"]
            answer=subprocess.run(base+["FLUSHDB"],capture_output=True,text=True,timeout=10,check=True).stdout.strip()
            if answer != "OK":
                raise RuntimeError("owned Redis state reset did not acknowledge")
            counts[name]=[]
            for _ in range(2):
                self.timed_call('application_reset_fixed_wait',time.sleep,1)
                counts[name].append(int(subprocess.run(base+["DBSIZE"],capture_output=True,text=True,timeout=10,check=True).stdout.strip()))
        receipt={"namespace":namespace,"scope":"only study-owned cart Redis data","phase":phase,
                 "remaining_keys":counts,"status":"empty" if all(v==[0,0] for v in counts.values()) else "failed"}
        (directory/("application_state_"+phase+".json")).write_text(json.dumps(receipt,indent=2)+"\n")
        if any(v != [0,0] for v in counts.values()):
            raise RuntimeError("cart state changed after traffic stopped; reset not proven")

    def configure_background(self, case, directory):
        """Bind registered background streams without starting traffic or faults."""
        self.active_background_workloads=copy.deepcopy(self.config.get('background_workloads',{}))
        self.active_background_bindings={}
        binding_receipts=[]
        databases={e['name'] for e in self.config['inventory']['entities'] if e['role']=='shared_dependency' and e.get('kind')=='Deployment'}
        for label,name in case.get('background_bindings',{}).items():
            if (self.config['native_application']!='boutique' or label not in self.active_background_workloads
                    or name not in databases or name not in ('shared-db','private-db')):
                raise ValueError('unregistered case background binding')
            service=self.client.get('Service',name);deployment=self.client.get('Deployment',name)
            selector=service['spec'].get('selector',{});labels=deployment['spec']['template']['metadata'].get('labels',{})
            if not selector or any(labels.get(k)!=v for k,v in selector.items()):raise ValueError('background Service does not select its owned database')
            self.active_background_workloads[label]['url']='redis://'+service['spec']['clusterIP']+':6379'
            self.active_background_bindings[label]=name
            binding_receipts.append({'stream':label,'service':name,'namespace':self.client.namespace,
                'service_uid':service['metadata']['uid'],'service_resource_version':service['metadata'].get('resourceVersion'),
                'cluster_ip':service['spec']['clusterIP'],'observed_at':time.time()})
        if binding_receipts:
            (Path(directory)/'case_background_bindings.json').write_text(json.dumps(binding_receipts,indent=2)+'\n')

    def start(self, case, directory):
        self.current_dir = directory
        self.setup_mutations, self.executors, self.baselines = [], [], {}
        self.baseline_context_mutations = []
        self.case = case
        self.fault_window = None
        self.provider.references = {}
        self.provider.raw_probes = []
        self.configure_background(case,directory)
        self.reset_application_state(directory,"before")
        self.offered_rps = case.get("offered_rps",self.config["workloads"]["business"]["offered_rps"])
        for name, workload in self.workloads.items():
            if name == "business":
                workload.offered_rps = self.offered_rps
            elif name == "peer":
                workload.offered_rps = case.get("peer_rps",self.offered_rps)
        super().start(case,directory)

    def baseline_complete(self, case):
        declaration = next(e for e in self.config["inventory"]["entities"] if e["id"]=="new")
        actual = self.client.get("Deployment",declaration["name"])["spec"]["template"]
        approved = self.policy.rollback_options["new"]["known_good"]["template"]
        if actual != approved:
            raise ValueError("historical native reference must measure the actual registered rollback template")
        graph = self.provider.collect()
        spec = next(p for p in self.provider.probes if p.id == "new_capacity")
        capacity = self.provider.probe(spec, graph)
        if isinstance(capacity, GraphObservation):
            values = {f.variable:f.value for f in capacity.facts}
            amount, stamp = values["capacity"], max(f.observed_at for f in capacity.facts)
        else:
            amount, stamp, values = capacity.value, capacity.observed_at, {}
        if amount is None:
            raise RuntimeError("approved native version has no measured functional capacity")
        self.provider.references = {"healthy": True, "capacity":amount,"observed_at":stamp}
        if "latency_ms" in values:
            self.provider.references["latency_ms"] = values["latency_ms"]
        (self.current_dir/"version_reference.json").write_text(json.dumps({**self.provider.references,
             "evidence":"baseline business window plus native direct capacity probe"},indent=2)+"\n")

    def verify(self, graph, plan, execution, completed_at):
        outcome = super().verify(graph,plan,execution,completed_at)
        verification = outcome["verification"]
        windows = verification["business"]["raw_windows"]
        baseline = self.baselines["business"]
        capacity_ok = all(w["throughput_rps"] >= baseline.throughput_rps*self.protocol.throughput_fraction for w in windows)
        verification["capacity_status"] = "pass" if capacity_ok else "fail"
        verification["capacity_probe_evidence"] = {"source":"independent_post_transaction_throughput", "window_ids":[w["window_id"] for w in windows]}
        # Hidden fault assignment is used only here, after prediction and execution.
        mechanism = True
        checks = []
        for fault in self.case.get("fault_changes",[]):
            e = graph.entity(fault["entity"])
            obj = self.client.get(e.kind,e.name)
            from .contracts import field_value
            current = field_value(obj,fault["path"])
            changed = current != fault["value"]
            checks.append({"entity":e.id,"field":fault["path"],"injected_value_still_present":not changed})
            mechanism &= changed
        verification["mechanism_eliminated"] = "pass" if mechanism else "fail"
        verification["mechanism_probe_evidence"] = {"source":"independent_post_configuration_observation", "checks":checks}
        verification["fault_elimination"] = "pass" if mechanism and verification["mitigation"]=="pass" else "fail"
        outcome["effects"]["capacity_sufficient"] = capacity_ok
        outcome["effects"]["mechanism_eliminated"] = mechanism
        return outcome

    def close_traffic(self):
        self.timed_call('traffic_shutdown',super().close_traffic)
        self.reset_application_state(self.current_dir,"after")
        (self.current_dir/"native_probe_measurements.json").write_text(json.dumps(self.provider.raw_probes,indent=2,allow_nan=False)+"\n")
