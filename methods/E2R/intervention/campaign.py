"""Matched intervention acquisition with separate predictor inputs and outcome records."""
from __future__ import annotations

import copy
import json
import random
import time
import urllib.request
import urllib.parse
import http.cookiejar
import math
import traceback
import shutil
import threading
import queue
from dataclasses import asdict, replace
from pathlib import Path

from rca_bench.workload_oracle import HTTPWindowCollector
from .contracts import Mutation, Policy, compensation, field_value
from .platform import Executor, KubectlClient, LiveProvider, Probe
from .schema import EFFECTS, EvidenceGraph, Plan, GraphObservation, merge_observation, input_bundle, number
from .verification import VerificationProtocol, business_windows, verification_package, execution_overlap_windows, conjunction
from .traffic import ConcurrentTraffic

_HTTP_TRANSPORTS = threading.local()


def http_transport():
    """Reuse handler/SSL setup per worker, with a fresh cookie session per transaction.

    urllib builds a default HTTPS SSLContext even for an HTTP-only request.
    Rebuilding that handler on every transaction retains large opener/handler
    cycles until GC; the fixed worker pool bounds the number of cached openers.
    urllib still creates/closes the TCP connection for each HTTP request.
    """
    if not hasattr(_HTTP_TRANSPORTS, 'state'):
        jar=http.cookiejar.CookieJar()
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                           urllib.request.HTTPCookieProcessor(jar))
        _HTTP_TRANSPORTS.state=(opener,jar)
    opener,jar=_HTTP_TRANSPORTS.state
    jar.clear()
    return opener,jar


class FaultNotDegraded(RuntimeError):
    """The declared perturbation did not cross the preregistered incident criterion."""


def append_json(path, value):
    with Path(path).open("a") as stream:
        stream.write(json.dumps(value, allow_nan=False) + "\n")
        stream.flush()
        import os
        os.fsync(stream.fileno())


def http_request(spec, detail=None, grpc_channel=None):
    """A registered transaction; optional application-level JSON success predicate."""
    if spec.get("protocol") == "native_cart_grpc":
        from .cart_transaction import cart_transaction
        return cart_transaction(spec, detail=detail, channel=grpc_channel)
    if spec.get("protocol") == "redis_kv":
        from .redis_transaction import redis_transaction
        return redis_transaction(spec,detail=detail)
    detail = {} if detail is None else detail
    started = time.perf_counter()
    try:
        opener, _ = http_transport()
        previous = None
        for index,step in enumerate(spec.get("steps", [spec])):
            detail["step"] = index
            detail.pop("http_status",None)
            url = step.get("url", spec["url"].rstrip("/")+step.get("path", ""))
            body = step.get("body")
            if "body_from_previous" in step:
                body = previous
                for key in step["body_from_previous"]:
                    body = body[key]
                if step.get("body_values"):
                    body = list(body.values())
            data = json.dumps(body).encode() if body is not None else None
            content_type = "application/json"
            if "form" in step:
                data = urllib.parse.urlencode(step["form"]).encode()
                content_type = "application/x-www-form-urlencoded"
            request = urllib.request.Request(url, data=data, method=step.get("method", "GET"),
                headers={"Content-Type": content_type, "User-Agent": "E2R-independent-verification"})
            with opener.open(request, timeout=step.get("timeout", spec.get("timeout", 5))) as response:
                raw = response.read(1_000_001)
                detail["http_status"] = response.status
                ok = 200 <= response.status < 300 and len(raw) <= 1_000_000
            parsed = None
            if "success_path" in step or "minimum_data_length" in step or step.get("parse_json"):
                parsed = json.loads(raw)
            if ok and "success_path" in step:
                value = parsed
                for key in step["success_path"]:
                    value = value[key]
                ok = value == step["success_value"]
            if ok and "minimum_data_length" in step:
                ok = len(parsed.get("data") or []) >= step["minimum_data_length"]
            if ok and step.get("require_non_null_data_values"):
                values = parsed.get("data")
                values = list(values.values()) if isinstance(values, dict) else values
                ok = bool(values) and all(value is not None for value in values)
            if ok and "expected_data_items" in step:
                values = parsed.get("data")
                values = list(values.values()) if isinstance(values, dict) else values
                ok = isinstance(values, list) and sorted(values) == sorted(step["expected_data_items"])
            if step.get('station_goal_check'):
                detail.setdefault('station_goal_observations',[]).append({'path':step.get('path'),'query':body,
                    'response_status':parsed.get('status') if isinstance(parsed,dict) else None,
                    'observed_data':parsed.get('data') if isinstance(parsed,dict) else None,
                    'expected_data_items':step.get('expected_data_items'),'goal_confirmed':bool(ok)})
            if ok and "contains" in step:
                ok = all(text.encode() in raw for text in step["contains"])
            if not ok:
                detail["failure"] = "response_content_or_size_predicate"
                break
            previous = parsed
    except Exception as exc:
        ok = False
        detail["failure"] = type(exc).__name__
        if hasattr(exc,"code"):
            detail["http_status"] = exc.code
        reason=getattr(exc,"reason",None)
        if reason is not None:
            detail["reason_type"]=type(reason).__name__
            if isinstance(getattr(reason,"errno",None),int):
                detail["errno"]=reason.errno
    return ok, (time.perf_counter()-started)*1000


class TransactionCollector(HTTPWindowCollector):
    def __init__(self,spec):
        self.spec = copy.deepcopy(spec)
        size=spec.get('connection_pool_size',0)
        if type(size) is not int or not 0<=size<=64 or size and spec.get('protocol')!='native_cart_grpc':
            raise ValueError('bounded connection pooling is supported only for registered native Cart RPCs')
        self.channels=[];self.channel_cursor=0;self.channel_lock=threading.Lock()
        self.redis_connection_budget=spec.get('client_connection_budget',96)
        if type(self.redis_connection_budget) is not int or not 1<=self.redis_connection_budget<=512:
            raise ValueError('client connection budget must be an integer between 1 and 512')
        self.redis_slots=queue.Queue()
        for index in range(self.redis_connection_budget):self.redis_slots.put(index)
        super().__init__(url=spec["url"],source_id=spec["source_id"],request_count=spec.get("requests",30),
            workers=spec.get("workers",5),window_duration_seconds=spec.get("duration"),offered_rps=spec.get("offered_rps"),
            timeout=spec.get("timeout",5),state_canary=(lambda:http_request(self.spec["canary"])[0]) if spec.get("canary") else None)

    def _request(self):
        ok,latency,_=self.request_with_details()
        return ok,latency

    def request_with_details(self):
        started=time.perf_counter()
        details={}
        if self.spec.get('protocol')=='redis_kv':
            from .redis_transaction import redis_transaction
            began=time.monotonic();deadline=began+self.timeout
            details['client_connection_budget']=self.redis_connection_budget
            try:slot=self.redis_slots.get(timeout=min(.25,self.timeout))
            except queue.Empty:
                details.update(failure='client_connection_budget_exhausted',client_slot_wait_seconds=time.monotonic()-began)
                return False,(time.perf_counter()-started)*1000,details
            try:
                details.update(client_slot_wait_seconds=time.monotonic()-began,key_slot=slot,
                               client_slots_in_use_observed=self.redis_connection_budget-self.redis_slots.qsize())
                ok,_=redis_transaction({**self.spec,'key_slot':slot},detail=details,deadline=deadline)
                return ok,(time.perf_counter()-started)*1000,details
            finally:self.redis_slots.put(slot)
        channel=None
        if self.spec.get('connection_pool_size',0):
            import grpc
            with self.channel_lock:
                if not self.channels:
                    self.channels=[grpc.insecure_channel(self.spec['url'].removeprefix('grpc://'),
                        options=[('grpc.use_local_subchannel_pool',1),('grpc.enable_http_proxy',0)])
                        for _ in range(self.spec['connection_pool_size'])]
                channel=self.channels[self.channel_cursor%len(self.channels)];self.channel_cursor+=1
        ok,latency=http_request(self.spec,detail=details,grpc_channel=channel)
        return ok,(time.perf_counter()-started)*1000,details

    def close_transports(self):
        with self.channel_lock:
            for channel in self.channels:channel.close()
            self.channels=[]


def collector(spec):
    return TransactionCollector(spec)


class KubernetesBackend:
    """Experimental lifecycle adapter for an explicitly named isolated namespace.

    Setup mutations belong to the evaluator. They are never present in model input.
    Repairs use the same trusted Executor as normal decisions.
    """
    source = "live"

    def __init__(self, config, out_dir):
        if config.get("experimental_namespace") != config["inventory"]["namespace"]:
            raise ValueError("explicit experimental namespace must match inventory")
        self.config, self.out_dir = config, Path(out_dir)
        self.client = KubectlClient(config["experimental_namespace"], config.get("kubeconfig"))
        self.provider = LiveProvider(config["inventory"], self.client)
        self.policy = Policy.from_dict(config["policy"])
        if self.policy.namespace != self.client.namespace:
            raise ValueError("policy namespace mismatch")
        self.protocol = VerificationProtocol(**config.get("verification", {}))
        self.workloads = {name: collector(spec) for name, spec in config["workloads"].items()}
        if config.get("continuous_workload", False) and any(
                spec.get("duration", 0) <= 0 or spec.get("offered_rps", 0) <= 0 for spec in config["workloads"].values()):
            raise ValueError("continuous workloads require explicit bounded duration and offered_rps")
        if "business" not in self.workloads or len(self.workloads) < 2:
            raise ValueError("one business workload and at least one non-target workload are required")
        self.setup_mutations, self.executors = [], []
        self.baselines = {}
        self.current_dir = None
        self.traffic = None
        self.query_observations = []
        self.baseline_context_mutations = []

    def _setup(self, changes):
        graph = self.provider.collect()
        for change in changes:
            entity = graph.entity(change["entity"])
            path = change["path"]
            permitted = {"Service": {"/spec/selector"}, "Deployment": {"/spec/replicas", "/spec/template"}}
            if entity.id not in self.policy.allowed_targets or path not in permitted.get(entity.kind, set()):
                raise ValueError("experimental setup write outside explicit field scope")
            live = self.client.get(entity.kind, entity.name)
            meta = live["metadata"]
            if meta["uid"] != entity.uid:
                raise ValueError("setup object identity changed")
            before = copy.deepcopy(field_value(live, path))
            if before == change["value"]:
                # A baseline replay may already be at the desired value. Do
                # not send a no-op JSON replace to the API; it creates a
                # needless rollout and some admission stacks reject it.
                append_json(self.current_dir / "setup_private.jsonl",
                            {"event":"skipped_noop","entity":change["entity"],"kind":entity.kind,
                             "name":entity.name,"path":path,"value":before,"uid":entity.uid})
                continue
            patch = [{"op": "test", "path": "/metadata/uid", "value": entity.uid},
                     {"op": "test", "path": "/metadata/resourceVersion", "value": meta["resourceVersion"]},
                     {"op": "test", "path": path, "value": before},
                     {"op": "replace", "path": path, "value": change["value"]}]
            mutation = Mutation(entity.id, entity.kind, entity.namespace, entity.name, entity.uid,
                                path, before, copy.deepcopy(change["value"]), patch)
            journal = self.current_dir / "setup_private.jsonl"
            if not journal.exists():
                journal.touch(mode=0o600)
            append_json(journal, {"event": "prepared", "mutation": asdict(mutation)})
            # Register uncertain writes too: restoration checks current fields before touching them.
            self.setup_mutations.append(mutation)
            accepted = self.client.patch(entity.kind, entity.name, patch)
            accepted_value = field_value(accepted, path)
            if accepted["metadata"]["uid"] == entity.uid:
                mutation.after = copy.deepcopy(accepted_value)
                append_json(journal, {"event":"api_accepted","mutation":asdict(mutation)})
            if accepted["metadata"]["uid"] != entity.uid or accepted_value != change["value"]:
                raise RuntimeError("setup write semantics changed by API processing")

    def timed_call(self, name, function, *args, **kwargs):
        started=time.perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            elapsed=time.perf_counter()-started
            if not hasattr(self,'phase_timings'):self.phase_timings={}
            self.phase_timings[name]=self.phase_timings.get(name,0.)+elapsed
            if getattr(self,'current_dir',None):
                append_json(self.current_dir/'phase_events.jsonl',{'phase':name,'seconds':elapsed,'completed_at':time.time()})

    def start(self, case, directory):
        self.current_dir = Path(directory)
        self.setup_mutations, self.executors, self.baselines = [], [], {}
        self.baseline_context_mutations = []
        self._setup(case.get("baseline_changes", []))
        if self.config.get("retain_baseline_context",False):
            self.baseline_context_mutations.extend(self.setup_mutations)
            self.setup_mutations = []
        if not self.wait_controllers():
            raise TimeoutError("baseline controllers did not settle before workload measurement")
        # Explicit setup settle is measured separately, never included as model latency.
        self.timed_call('baseline_fixed_wait',time.sleep,case.get("baseline_settle_seconds", 0))
        if hasattr(self,'prepare_workload_state'):
            self.timed_call('prepare_acknowledged_workload_state',self.prepare_workload_state,case)
        if self.config.get("continuous_workload", False):
            self.start_traffic()
            after = time.time()
        else:
            after = None
        stable = 0
        required = self.config.get("baseline_consecutive_windows",1)
        health_timeout = self.config.get("baseline_health_timeout",0)
        if type(required) is not int or required<1 or required>1 and health_timeout<=0:
            raise ValueError("baseline stabilization requires a positive bounded timeout")
        if required>1 and not self.traffic:
            raise ValueError("consecutive baseline cohorts require continuous concurrent workloads")
        deadline = time.monotonic()+health_timeout if health_timeout else None
        baseline_ceiling = self.config.get('baseline_latency_ceiling_ms')
        if baseline_ceiling is not None and (not number(baseline_ceiling) or baseline_ceiling <= 0):
            raise ValueError('baseline latency ceiling must be finite and positive')
        previous_end = None
        while True:
            timeout = max(1,deadline-time.monotonic()) if deadline else self.config.get("measurement_timeout",180)
            baseline_cycle = self.traffic.take(after,1,timeout)[0] if self.traffic else {
                name:workload.collect("baseline-"+name) for name,workload in self.workloads.items()}
            wall = time.time()
            def eligible(name,w):
                measured_at=w.measurement_completed_at if w.measurement_completed_at is not None else w.end_time
                return (type(w.samples) is int and w.samples>=self.protocol.min_samples
                    and all(number(v) for v in (w.start_time,w.end_time,measured_at,w.p95_ms,w.throughput_rps,w.error_rate))
                    and w.start_time<w.end_time<=measured_at<=wall and w.p95_ms>0 and w.throughput_rps>0
                    and 0<=w.error_rate<=self.config.get("max_baseline_error",.01) and w.state_canary_ok is True
                    and w.source_id==self.workloads[name].source_id
                    and (baseline_ceiling is None or w.p95_ms<=baseline_ceiling))
            healthy = all(eligible(name,w) for name,(w,_) in baseline_cycle.items())
            if self.traffic:
                starts=[w.start_time for w,_ in baseline_cycle.values()]
                ends=[w.end_time for w,_ in baseline_cycle.values()]
                healthy &= max(starts)-min(starts)<1e-6 and max(ends)-min(ends)<1e-6
                if previous_end is not None and abs(min(starts)-previous_end)>1e-6:
                    stable=0
                previous_end=max(ends)
            timely = deadline is None or time.monotonic() <= deadline
            healthy &= timely
            stable = stable+1 if healthy else 0
            append_json(self.current_dir/"baseline_acquisition.jsonl",{"healthy":healthy,"consecutive_healthy":stable,
                "required":required,"within_time_budget":timely,"latency_ceiling_ms":baseline_ceiling,
                "measurements":{name:{"window":asdict(w),"raw":raw} for name,(w,raw) in baseline_cycle.items()}})
            if stable>=required:
                break
            if deadline is None or time.monotonic()>=deadline:
                raise RuntimeError("representative workload not stably healthy before injection")
            after = max(w.end_time for w,_ in baseline_cycle.values())
        for name, (window, raw) in baseline_cycle.items():
            self.baselines[name] = window
            (self.current_dir / f"baseline_{name}.json").write_text(json.dumps({"window": asdict(window), "raw": raw}, allow_nan=False))
            if window.error_rate > self.config.get("max_baseline_error", 0.01) or window.state_canary_ok is not True:
                raise RuntimeError("representative workload not healthy before injection")
        if hasattr(self, "baseline_complete"):
            self.baseline_complete(case)
        self._setup(case.get("fault_changes", []))
        if case.get("fault_wait_entities") and not self.wait_fault_controllers(set(case["fault_wait_entities"])):
            raise TimeoutError("registered fault target controllers did not settle")
        self.timed_call('fault_fixed_wait',time.sleep,case.get("fault_settle_seconds", 0))
        if self.traffic:
            fault, raw = self.traffic.take(time.time(), 1, self.config.get("measurement_timeout", 180))[0]["business"]
        else:
            fault, raw = self.workloads["business"].collect("fault-validation")
        (self.current_dir / "fault_window.json").write_text(json.dumps({"window": asdict(fault), "raw": raw}, allow_nan=False))
        self.fault_window = fault
        base = self.baselines["business"]
        degraded = (fault.error_rate > base.error_rate + self.protocol.error_margin
                    or fault.p95_ms > max(base.p95_ms * self.protocol.latency_multiplier,self.protocol.latency_floor_ms)
                    or fault.throughput_rps < base.throughput_rps * self.protocol.throughput_fraction
                    or fault.state_canary_ok is False)
        if not degraded:
            raise FaultNotDegraded("fault did not measurably degrade the registered business transaction")

    def wait_controllers(self, entity_ids=None):
        deadline = time.monotonic() + self.config.get("convergence_timeout", 60)
        while time.monotonic() < deadline:
            ready = True
            names = set()
            for declaration in self.config["inventory"]["entities"]:
                if declaration.get("kind") != "Deployment" or entity_ids is not None and declaration["id"] not in entity_ids:
                    continue
                names.add(declaration["name"])
                raw = self.client.get("Deployment", declaration["name"])
                desired, status = raw["spec"].get("replicas", 1), raw.get("status", {})
                ready &= (status.get("observedGeneration", 0) >= raw["metadata"].get("generation", 1)
                          and status.get("readyReplicas", 0) == desired
                          and status.get("updatedReplicas", 0) == desired
                          and status.get("replicas", 0) == desired)
            # Old terminating processes can retain shared DB connections.
            ready &= not any(p["metadata"].get("deletionTimestamp") and p["metadata"].get("labels",{}).get("app") in names
                             for p in self.client.list("Pod"))
            if ready:
                return True
            time.sleep(min(1, max(0, deadline-time.monotonic())))
        return False

    def wait_fault_controllers(self, entity_ids):
        """Wait for the requested fault template to roll out without requiring it to be Ready."""
        deadline=time.monotonic()+self.config.get("convergence_timeout",60)
        wanted={d['id']:d for d in self.config['inventory']['entities'] if d.get('kind')=='Deployment' and d['id'] in entity_ids}
        while time.monotonic()<deadline:
            settled=True
            for declaration in wanted.values():
                raw=self.client.get('Deployment',declaration['name']);status=raw.get('status',{});desired=raw['spec'].get('replicas',1)
                settled &= (status.get('observedGeneration',0)>=raw['metadata'].get('generation',1)
                            and status.get('updatedReplicas',0)==desired and status.get('replicas',0)==desired
                            and not any(p['metadata'].get('deletionTimestamp') for p in self.client.list('Pod','app='+declaration['name'])))
            if settled:return True
            time.sleep(min(1,max(0,deadline-time.monotonic())))
        return False

    def observe(self, case):
        graph = self.provider.collect()
        self.masked_input_graph = graph.to_dict()
        self.observation_strategy_started_at = time.time()
        self.query_observations = []
        requested = set(self.config.get("initial_probes", []))
        if not requested <= {p.id for p in self.provider.probes}:
            raise ValueError("unregistered initial probe")
        order=case.get('initial_probe_order',self.config.get('initial_probe_order'))
        if order is not None and (len(order)!=len(set(order)) or set(order)!=requested):
            raise ValueError('initial probe order must contain exactly the registered initial probes')
        catalog={p.id:p for p in self.provider.probes}
        ordered=[catalog[k] for k in order] if order is not None else self.provider.probes
        for probe in ordered:
            if probe.id in requested:
                started = time.perf_counter()
                fact = self.provider.probe(probe, graph)
                self.query_observations.append({"probe": asdict(probe), "observation": asdict(fact),
                    "observation_type": "graph" if isinstance(fact, GraphObservation) else "fact",
                    "seconds": time.perf_counter()-started})
                if isinstance(fact, GraphObservation):
                    graph = merge_observation(graph, fact, time.time())
                else:
                    graph.facts.append(fact)
                    graph.at = time.time()
        graph.__post_init__()
        bundle = {"schema": "e2r_intervention_v1", "graph": graph.to_dict(), "plans": copy.deepcopy(self.config["plans"])}
        input_bundle(bundle)
        return bundle

    def execute(self, graph, plan, directory):
        self.execution_started_at = time.time()
        executor = Executor(self.client, self.provider, self.policy, Path(directory)/"execution_private.jsonl",
                            convergence_timeout=self.config.get("convergence_timeout", 60))
        self.executors.append(executor)
        return executor.execute(graph, plan, dry_run=False)

    def verify(self, graph, plan, execution, completed_at):
        measured = {}
        if self.traffic:
            cycles = self.traffic.take(completed_at, self.protocol.required_windows,
                                      self.config.get("measurement_timeout", 180))
            measured = {name: ([cycle[name][0] for cycle in cycles], [cycle[name][1] for cycle in cycles])
                        for name in self.workloads}
        for name, workload in (() if self.traffic else self.workloads.items()):
            windows, raw = [], []
            for i in range(self.protocol.required_windows):
                w, r = workload.collect(f"post-{name}-{i}")
                windows.append(w)
                raw.append(r)
            measured[name] = (windows, raw)
        now = time.time()
        evaluated = {name: business_windows(self.baselines[name], windows, completed_at=completed_at,
                                           evaluated_at=now, protocol=self.protocol)
                     for name, (windows, _) in measured.items()}
        for name, (windows, raw) in measured.items():
            evaluated[name]["raw_windows"] = [asdict(w) for w in windows]
            (self.current_dir / f"post_{name}.json").write_text(json.dumps({"windows": [asdict(w) for w in windows], "raw": raw}, allow_nan=False))
        write_status = "pass" if not plan.actions or all(s.get("write_verified") for s in execution.get("steps", [])) else "fail"
        converged = all(s.get("controller_converged") for s in execution.get("steps", [])) if plan.actions else False
        if not plan.actions:
            services = [e for e in graph.entities if e.id in graph.goals and e.kind == "Service"]
            converged = bool(services) and all((self.provider.ready_endpoints(e.name, None) or 0) > 0 for e in services)
        package = verification_package(field_status=write_status, controller_status="pass" if converged else "fail",
            business=evaluated.pop("business"), non_target=evaluated, mechanism_status="unknown")
        if self.config.get("monitor_execution_safety",False):
            from rca_bench.recovery_oracle import WorkloadWindow
            history = self.traffic.history() if self.traffic else []
            intervals=[]
            if plan.actions:
                intervals.append(('during_execution_non_target',self.execution_started_at,completed_at))
            if hasattr(self,'strategy_started_at') and hasattr(self,'strategy_completed_at'):
                # Queries can harm peers even when the final decision abstains.
                intervals.append(('during_strategy_non_target',self.strategy_started_at,self.strategy_completed_at))
            interval_statuses=[]
            for label,interval_start,interval_end in intervals:
                during={}
                for name in self.workloads:
                    if name=="business":continue
                    windows = [WorkloadWindow(**cycle[name]["window"]) for cycle in history
                               if cycle[name]["window"]["start_time"] < interval_end
                               and cycle[name]["window"]["end_time"] > interval_start]
                    result = execution_overlap_windows(self.baselines[name],windows,started_at=interval_start,
                        completed_at=interval_end,evaluated_at=time.time(),protocol=self.protocol)
                    result["raw_windows"] = [asdict(w) for w in windows]
                    during[name] = result
                package[label]=during
                interval_statuses.extend(r['status'] for r in during.values())
            if intervals:
                package["non_target_safety"] = conjunction([package["non_target_safety"],*interval_statuses])
                package["mitigation"] = conjunction([package["write_semantics"],package["controller_convergence"],
                                                    package["business"]["status"],package["non_target_safety"]])
        # Measure the same endpoint effect independently for every arm, including scale/rollback/no-op.
        endpoint_evidence = []
        for entity in graph.entities:
            if entity.id in graph.goals and entity.kind == "Service":
                live = self.client.get("Service", entity.name)
                count = self.provider.ready_endpoints(entity.name, live.get("spec", {}).get("selector"))
                endpoint_evidence.append({"entity": entity.id, "count": count, "observed_at": time.time()})
        package["endpoint_evidence"] = endpoint_evidence
        package["endpoints_status"] = ("unknown" if not endpoint_evidence or any(r["count"] is None for r in endpoint_evidence)
                                        else "pass" if all(r["count"] > 0 for r in endpoint_evidence) else "fail")
        package["capacity_status"] = "unknown"  # Requires an independently registered post-action capacity probe.
        for key, variable in (("capacity_probe", "capacity_status"), ("mechanism_probe", "mechanism_eliminated")):
            if key in self.config:
                spec = next(p for p in self.provider.post_probes if p.id == self.config[key])
                observation = self.provider.observe_post(spec)
                value = observation["value"]
                package[variable] = "pass" if value is True else "fail" if value is False else "unknown"
                package[key + "_evidence"] = observation
        package["fault_elimination"] = "pass" if package["mitigation"] == "pass" and package["mechanism_eliminated"] == "pass" else "fail" if package["mechanism_eliminated"] == "fail" else "unknown"
        statuses = [package["endpoints_status"], package["capacity_status"], package["business"]["status"],
                    package["non_target_safety"], package["mechanism_eliminated"]]
        # Continuous numerical targets, not copies of the five effect labels.
        business_raw = measured["business"][0]
        peer_raw = [w for name, (windows, _) in measured.items() if name != "business" for w in windows]
        ready = sum(self.client.get("Deployment", e.name).get("status", {}).get("readyReplicas", 0)
                    for e in graph.entities if e.kind == "Deployment")
        endpoint_count = sum(row["count"] or 0 for row in endpoint_evidence)
        state_targets = [math.log1p(endpoint_count), math.log1p(ready),
                         sum(w.error_rate for w in business_raw)/len(business_raw),
                         sum(w.error_rate for w in peer_raw)/len(peer_raw),
                         math.log1p(sum(w.p95_ms for w in business_raw)/len(business_raw))]
        return {"observed_at": time.time(), "execution_completed_at": completed_at,
                "action_started_at": getattr(self,"execution_started_at",completed_at),
                **({'strategy_started_at':self.strategy_started_at,'strategy_completed_at':self.strategy_completed_at}
                   if hasattr(self,'strategy_started_at') and hasattr(self,'strategy_completed_at') else {}),
                "state_targets": state_targets,
                "state_target_definition": ["log1p_ready_endpoints", "log1p_ready_replicas", "business_error_fraction",
                                            "peer_error_fraction", "log1p_business_p95_ms"],
                "verification_protocol": asdict(self.protocol),
                "baseline_windows": {key: asdict(value) for key, value in self.baselines.items()},
                "verification": package,
                "effects": {key: True if status == "pass" else False if status == "fail" else None for key, status in zip(EFFECTS, statuses)}}

    def start_traffic(self):
        if self.traffic is not None:
            raise RuntimeError("previous workload session has not drained")
        if self.config.get("traffic_process", False):
            from .traffic_process import ProcessTraffic
            specs = {n:{**w.spec,"offered_rps":w.offered_rps} for n,w in self.workloads.items()}
            background=getattr(self,'active_background_workloads',self.config.get('background_workloads',{}))
            if set(background)&set(specs):raise ValueError('background and measured stream names overlap')
            self.traffic = ProcessTraffic({**specs,**background},observed_streams=self.workloads,
                                          enable_cyclic_gc=self.config.get('traffic_gc_enabled',True),
                                          dispatch_delay_floor_seconds=self.config.get('dispatch_delay_floor_seconds',.25))
        else:
            self.traffic = ConcurrentTraffic(self.workloads,
                dispatch_delay_floor_seconds=self.config.get('dispatch_delay_floor_seconds',.25))
        self.traffic.start()

    def prepare_reset_health(self):
        """Optional cleanup-only workload scope after original fields converge."""

    def restore(self):
        checks = []
        if self.config.get("direct_baseline_restore",False):
            changes = self.setup_mutations + [m for executor in self.executors for m in executor.applied]
            result = self.timed_call('joint_fault_action_field_restore',self.restore_recorded_fields,changes)
            checks += result
            if any(not r["restored"] for r in result):
                return {"status":"failed","checks":checks}
            # Baseline evaluator writes are a separate outer layer. Restore
            # them after fault/action writes, so an interrupted campaign cannot
            # leave its last fault template active for the next run.
            if self.baseline_context_mutations:
                result = self.timed_call('baseline_context_field_restore',self.restore_recorded_fields,self.baseline_context_mutations)
                checks += result
                if any(not r["restored"] for r in result):
                    return {"status":"failed","checks":checks}
        for executor in (() if self.config.get("direct_baseline_restore",False) else reversed(self.executors)):
            restored = self.timed_call('action_compensation',executor.compensate)
            checks += restored
            if any(not r["restored"] for r in restored):
                return {"status": "failed", "checks": checks}
        for mutation in (() if self.config.get("direct_baseline_restore",False) else reversed(self.setup_mutations)):
            try:
                live = self.client.get(mutation.kind, mutation.name)
                if live["metadata"]["uid"] == mutation.uid and field_value(live, mutation.path) == mutation.before:
                    checks.append({"entity": mutation.entity, "restored": True, "already_original": True})
                    continue
                accepted = self.client.patch(mutation.kind, mutation.name, compensation(mutation, live))
                good = accepted["metadata"]["uid"] == mutation.uid and field_value(accepted, mutation.path) == mutation.before
                checks.append({"entity": mutation.entity, "restored": good})
                if not good:
                    return {"status": "failed", "checks": checks}
            except Exception as exc:
                return {"status": "failed", "checks": checks, "error": type(exc).__name__}
        # Field restoration alone is insufficient to start another independent trial.
        ready = self.timed_call('reset_controller_wait',self.wait_controllers)
        checks.append({"controller_restore": bool(ready)})
        if not ready:
            return {"status": "failed", "checks": checks}
        if self.baselines:
            self.prepare_reset_health()
        if self.traffic and self.traffic.diagnostics().get("error"):
            # Retain the invalid measurement, drain it, and independently verify
            # reset with fresh traffic. The failed episode remains failed.
            KubernetesBackend.close_traffic(self)
            if self.baselines:
                self.start_traffic()
        if self.baselines:
            reset_started = time.time()
            deadline = time.monotonic() + self.config.get("reset_health_timeout", 180)
            after = reset_started
            while time.monotonic() < deadline:
                health, measured = {}, {}
                try:
                    cycle = self.timed_call('reset_business_window_wait',self.traffic.take,after,1,max(1,deadline-time.monotonic()))[0] if self.traffic else None
                except TimeoutError:
                    return {"status": "failed", "checks": checks, "error": "reset_health_timeout"}
                for name, workload in self.workloads.items():
                    window, raw = cycle[name] if cycle else self.timed_call('reset_business_window_wait',workload.collect,"reset-" + name)
                    measured[name] = {"window": asdict(window), "raw": raw}
                    health[name] = business_windows(self.baselines[name], [window], completed_at=reset_started,
                          evaluated_at=time.time(), protocol=replace(self.protocol, required_windows=1))
                checks.append({"reset_business_gate": health, "measurements": measured})
                if all(v["status"] == "pass" for v in health.values()):
                    break
                after = max(v["window"]["end_time"] for v in measured.values())
            else:
                return {"status": "failed", "checks": checks}
        return {"status": "restored", "checks": checks}

    def restore_recorded_fields(self, mutations):
        """Restore the recorded baseline directly, without briefly re-injecting a fault.

        The field history must form a continuous chain. Every final patch still
        tests the actual UID, resourceVersion and exact current field value.
        """
        chains = {}
        for mutation in mutations:
            key = (mutation.kind,mutation.name,mutation.uid,mutation.path)
            chain = chains.setdefault(key,[])
            if chain and chain[-1].after != mutation.before:
                return [{"entity":mutation.entity,"restored":False,"error":"discontinuous_recorded_field_history"}]
            chain.append(mutation)
        results = []
        for chain in reversed(list(chains.values())):
            first,last = chain[0],chain[-1]
            error=None; ok=False
            for attempt in range(3):
                try:
                    live = self.client.get(first.kind,first.name)
                    current = field_value(live,first.path)
                    if live["metadata"]["uid"] != first.uid:
                        raise ValueError("resource identity changed")
                    if current == first.before:
                        ok = True
                    elif current in (last.after,last.before):
                        merged = replace(first,after=copy.deepcopy(current))
                        accepted = self.client.patch(first.kind,first.name,compensation(merged,live))
                        ok = accepted["metadata"]["uid"]==first.uid and field_value(accepted,first.path)==first.before
                    else:
                        raise ValueError("current field is outside the recorded mutation chain")
                    if ok:break
                except ValueError as exc:
                    error=str(exc)
                    break
                except Exception as exc:
                    error=type(exc).__name__
                    if attempt<2:time.sleep(1)
            row = {"entity":first.entity,"field":first.path,"restored":ok,"recorded_writes":len(chain)}
            if not ok:row["error"]=error or "restore_not_verified"
            results.append(row)
            append_json(self.current_dir/"direct_restore_receipts.jsonl",row)
            if not row["restored"]:
                break
        return results

    def finalize_campaign(self):
        if not self.baseline_context_mutations:
            return {"status":"not_needed"}
        self.current_dir = self.out_dir
        checks = self.restore_recorded_fields(self.baseline_context_mutations)
        ready = all(r["restored"] for r in checks) and self.wait_controllers()
        health = {}
        if ready:
            health = {name:http_request(spec["canary"])[0] for name,spec in self.config["workloads"].items()}
        result = {"status":"restored" if ready and all(health.values()) else "failed","checks":checks,"native_canaries":health}
        (self.out_dir/"campaign_baseline_restore.json").write_text(json.dumps(result,indent=2)+"\n")
        return result

    def close_traffic(self):
        if self.traffic:
            session=self.traffic
            try:
                session.stop()
            finally:
                # A timeout must retain ownership; reset cannot race live traffic.
                diagnostics=session.diagnostics()
                append_json(self.current_dir/"traffic_sessions.jsonl",diagnostics)
                history_path=self.current_dir/"continuous_traffic.json"
                previous=json.loads(history_path.read_text()) if history_path.exists() else []
                history_path.write_text(json.dumps(previous+session.history(),allow_nan=False)+"\n")
            self.traffic = None


def run_campaign(config, out_dir, backend_factory=KubernetesBackend):
    """Every started arm gets a ledger row, including setup/restore/unknown failures."""
    path = Path(out_dir)
    path.mkdir(parents=True, exist_ok=False)
    (path / "protocol.json").write_text(json.dumps(config, indent=2) + "\n")
    snapshot = path / "source_snapshot"
    snapshot.mkdir()
    for source in Path(__file__).parent.glob("*.py"):
        shutil.copy2(source, snapshot / source.name)
    from rca_bench import workload_oracle, recovery_oracle
    for module in (workload_oracle, recovery_oracle):
        shutil.copy2(module.__file__, snapshot / Path(module.__file__).name)
    jobs = []
    for case in config["cases"]:
        for repeat in range(config.get("repeats", 5)):
            for arm in config["arms"]:
                jobs.append((case, repeat, arm))
    if "scheduled_jobs" in config:
        lookup = {(c["group_id"],c["condition_id"],r,a):(c,r,a) for c,r,a in jobs}
        jobs = [lookup[(j["group_id"],j["condition_id"],j["repeat"],j["arm"])] for j in config["scheduled_jobs"]]
    else:
        random.Random(config.get("seed", 42)).shuffle(jobs)
    (path / "order.json").write_text(json.dumps([{"group_id": c["group_id"], "condition_id": c["condition_id"],
                            "repeat": r, "arm": a} for c, r, a in jobs], indent=2) + "\n")
    backend = backend_factory(config, path)
    results = []
    for index, (case, repeat, arm) in enumerate(jobs):
        if ('scheduled_jobs' in config and 'initial_probe_order' in config['scheduled_jobs'][index]) or config.get('synchronized_baseline_rollout_roles'):
            case=copy.deepcopy(case)
            if 'scheduled_jobs' in config and 'initial_probe_order' in config['scheduled_jobs'][index]:
                case['initial_probe_order']=config['scheduled_jobs'][index]['initial_probe_order']
            for role in config.get('synchronized_baseline_rollout_roles',[]):
                changes=[c for c in case.get('baseline_changes',[]) if c['entity']==role and c['path']=='/spec/template']
                if len(changes)!=1:raise ValueError('synchronized baseline rollout needs one registered template per role')
                slot=config['scheduled_jobs'][index]['original_schedule_slot'] if 'scheduled_jobs' in config else index
                changes[0]['value'].setdefault('metadata',{}).setdefault('annotations',{})['e2r.dev/trial']=str(config.get('seed',42))+'-'+str(slot)
        identity = f"episode-{index:06d}"
        directory = path / identity
        directory.mkdir()
        backend.phase_timings={}
        started = time.time()
        print(f"[{index+1}/{len(jobs)}] {identity} {arm} starting", flush=True)
        append_json(path / "attempts.jsonl", {"episode_id": identity, "status": "started", "started_at": started})
        row = {"episode_id": identity, "group_id": case["group_id"], "condition_id": case["condition_id"],
               "comparison_group": case.get("comparison_group", case["group_id"]),
               "application": config["application"], "repeat": repeat, "chosen_plan": arm,
               "source": backend.source, "status": "started", "timings_seconds": {}}
        if hasattr(backend,'choose_plan'):row['assigned_method']=arm
        elif getattr(backend,'observation_method',None):row['assigned_method']=backend.observation_method
        if "policy" in config:
            row["operation_policy"] = asdict(backend.policy)
        if "scheduled_jobs" in config:
            row["original_schedule_slot"] = config["scheduled_jobs"][index]["original_schedule_slot"]
        try:
            stage = time.perf_counter()
            backend.start(case, directory)
            row["timings_seconds"]["setup_and_fault_validation"] = time.perf_counter()-stage
            stage = time.perf_counter()
            row["input"] = backend.observe(case)
            row["timings_seconds"]["pre_action_observation"] = time.perf_counter()-stage
            if hasattr(backend, "query_observations"):
                row["available_pre_action_observations"] = backend.query_observations
                row["masked_graph"] = backend.masked_input_graph
            if "diagnosis_label" in case:
                row["diagnosis_label"] = case["diagnosis_label"]
            graph, plans = input_bundle(row["input"])
            if getattr(backend,'probe_accountant',None) is not None:
                backend.probe_accountant.mark_decision_boundary()
            if hasattr(backend, "choose_plan"):
                stage = time.perf_counter()
                row["assigned_method"] = arm
                decision = backend.choose_plan(graph, plans, arm)
                row["decision"] = decision
                row["chosen_plan"] = decision["selected_plan"] or "no_action"
                row["input"]["graph"] = decision["graph"]
                graph, plans = input_bundle(row["input"])
                row["timings_seconds"]["refinement_and_decision"] = time.perf_counter()-stage
            plan = next(p for p in plans if p.id == row["chosen_plan"])
            row["schema"] = "e2r_intervention_episode_v1"
            row["action_started_at"] = time.time()
            stage = time.perf_counter()
            row["execution"] = backend.execute(graph, plan, directory)
            completed_at = time.time()
            row["timings_seconds"]["execution_and_controller"] = time.perf_counter()-stage
            if row["execution"]["status"] in ("executed", "no_action") or (
                    row["execution"]["status"]=="controller_failed" and row["execution"].get("all_writes_applied") is True):
                stage = time.perf_counter()
                row["outcome"] = backend.verify(graph, plan, row["execution"], completed_at)
                if "state_targets" in row["outcome"]:
                    row["state_targets"] = row["outcome"]["state_targets"]
                row["timings_seconds"]["independent_verification"] = time.perf_counter()-stage
                row["status"] = "observed"
            else:
                row["status"] = row["execution"]["status"]
                row["outcome"] = {"observed_at": time.time(), "effects": {k: None for k in EFFECTS}, "verification": {}}
        except Exception as exc:
            status = "ineligible" if isinstance(exc,FaultNotDegraded) and config.get("record_nondegrading_attempts",False) else "failed"
            row.update(status=status, error=type(exc).__name__, detail=str(exc)[:500])
            if hasattr(backend, 'choose_plan') and 'decision' not in row:
                # An observation without a selected candidate is not an
                # action/outcome input. Retain it without inventing no_action.
                if 'input' in row:
                    row['preselection_input'] = row.pop('input')
                row['chosen_plan'] = None
                row['selection_status'] = 'failed_before_candidate_selected'
            (directory / "failure_traceback.txt").write_text(traceback.format_exc())
        finally:
            if "input" in row:
                row["schema"] = "e2r_intervention_episode_v1"
                row.setdefault("action_started_at", time.time())
                row.setdefault("execution", {"plan_id": row["chosen_plan"], "status": "not_executed"})
                row.setdefault("outcome", {"observed_at": time.time(), "effects": {k: None for k in EFFECTS}, "verification": {}})
            else:
                row["schema"] = "e2r_intervention_attempt_v1"
            stage = time.perf_counter()
            try:
                row["restore"] = backend.restore()
            except Exception as exc:
                row["restore"] = {"status": "failed", "error": type(exc).__name__}
                (directory / "restore_traceback.txt").write_text(traceback.format_exc())
            try:
                if hasattr(backend, "close_traffic"):
                    backend.close_traffic()
            except Exception as exc:
                row["restore"] = {"status": "failed", "error": "workload_shutdown:"+type(exc).__name__,
                                  "prior_restore":row["restore"]}
            row["timings_seconds"]["environment_restore"] = time.perf_counter()-stage
            row['phase_timings_seconds']=dict(backend.phase_timings)
            if getattr(backend,'probe_accountant',None) is not None:
                row['total_probe_budget']=backend.probe_accountant.report()
            row['phase_timing_scope']={'fixed_waits':'Explicit baseline/fault and Redis reset sleeps, nested in enclosing phases.',
                'field_restore':'Direct mode measures the merged fault/action field history together; never reinject a fault to separate clocks.',
                'reset':'Controller waiting, business-window waiting, traffic shutdown and application-data reset are measured separately.',
                'accounting':'These phase clocks are nested within top-level timings; do not add parent and child durations.'}
            row["completed_at"] = time.time()
            row["timings_seconds"]["total_attempt"] = row["completed_at"]-started
            if hasattr(backend,'stop_reason'):
                row['policy_stop_reason']=backend.stop_reason(row)
            (directory / "episode.json").write_text(json.dumps(row, indent=2, allow_nan=False) + "\n")
            append_json(path / "attempts.jsonl", {"episode_id": identity, "status": row["status"], "restore": row["restore"]["status"]})
            append_json(path / "episodes.jsonl", row)
            results.append(row)
            print(f"[{index+1}/{len(jobs)}] {identity} {row['status']} restore={row['restore']['status']}", flush=True)
        if row["restore"]["status"] != "restored" or row["status"] == "failed" or row.get('policy_stop_reason'):
            break
    final_restore = backend.finalize_campaign() if hasattr(backend,"finalize_campaign") else {"status":"not_needed"}
    summary = {"schema": "e2r_intervention_campaign_v1", "planned": len(jobs), "attempted": len(results),
               "observed": sum(r["status"] == "observed" for r in results),
               "nondegrading_attempts": sum(r["status"] == "ineligible" for r in results),
               "unknown_or_rejected": sum(r["status"] != "observed" for r in results),
               "campaign_baseline_restore": final_restore,
               "status": "complete" if len(results) == len(jobs) and final_restore["status"] in ("restored","not_needed")
                   and all(r["restore"]["status"] == "restored" and r["status"] != "failed" and not r.get('policy_stop_reason') for r in results) else "stopped"}
    (path / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary
