#!/usr/bin/env python3
"""Build a concrete native pilot from observed Kubernetes resources, before execution."""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.contracts import Policy
from methods.E2R.intervention.platform import KubectlClient, Probe
from methods.E2R.intervention.schema import Action, Plan


def endpoint(client,name):
    value=client.get("Service",name)
    return f"http://{value['spec']['clusterIP']}:{value['spec']['ports'][0]['port']}"


def transaction(application,url,source):
    if application=="boutique":
        url=url.replace("http://","grpc://")
        return {"url":url,"protocol":"native_cart_grpc","source_id":source,"timeout":3,
                "canary":{"url":url,"protocol":"native_cart_grpc","timeout":3,"read_only":True},
                "duration":10,"offered_rps":2,"workers":32,"requests":20}
    else:
        steps=[{"path":"/api/v1/stationservice/stations","success_path":["status"],"success_value":1,"minimum_data_length":13},
               {"path":"/api/v1/stationservice/stations/idlist","method":"POST","body":["shanghai","suzhou"],
                "success_path":["status"],"success_value":1,"minimum_data_length":2,"require_non_null_data_values":True},
               {"path":"/api/v1/stationservice/stations/namelist","method":"POST","body_from_previous":["data"],"body_values":True,
                "success_path":["status"],"success_value":1,"expected_data_items":["shanghai","suzhou"]}]
        canary={"url":url,"timeout":3,"steps":copy.deepcopy(steps)}
    return {"url":url,"source_id":source,"timeout":3,"steps":steps,"canary":canary,
            "duration":10,"offered_rps":2,"workers":32,"requests":20}


def build(application,out,repeats):
    ns={"boutique":"e2r-isg-boutique","trainticket":"e2r-isg-trainticket"}[application]
    client=KubectlClient(ns)
    prefix="cart" if application=="boutique" else "station"
    now=time.time()
    objects={role:client.get("Deployment",prefix+"-"+role) for role in ("old","new","peer")}
    if any(obj.get("status",{}).get("readyReplicas",0)!=obj["spec"].get("replicas",1) or obj["spec"].get("replicas",1)<1 for obj in objects.values()):
        raise RuntimeError("all three native versions/peer must be Ready before freezing the pilot")
    entities=[{"id":"entry","role":"service","kind":"Service","namespace":ns,"name":"target","binding":"business-route"},
              {"id":"db","role":"shared_dependency","kind":"Deployment","namespace":ns,"name":"shared-db","binding":"shared-storage"},
              {"id":"approved_new","role":"deployment","binding":"approved-active-template"}]
    entities += [{"id":role,"role":"deployment","kind":"Deployment","namespace":ns,"name":prefix+"-"+role,
                  "binding":{"old":"standby-service","new":"active-service","peer":"non-target-service"}[role]} for role in objects]
    relations=[{"source":"entry","target":target,"kind":"depends_on","observed_at":now,"ttl":1209600}
               for target in ("old","new","approved_new")]
    relations += [{"source":role,"target":"db","kind":"competes_for","observed_at":now,"ttl":1209600} for role in objects]
    probes=[Probe("old_health","old","healthy","native",[False,True],cost=1,timeout=10,ttl=120),
            Probe("old_capacity","old","capacity","native",[0.5,6.0],cost=3,timeout=12,ttl=120),
            Probe("new_capacity","new","capacity","native",[0.5,6.0],cost=3,timeout=12,ttl=120),
            Probe("old_cpu","old","cpu","native",[0.01,0.8],cost=1,timeout=5,ttl=120),
            Probe("peer_cpu","peer","cpu","native",[0.01,0.8],cost=1,timeout=5,ttl=120)]
    possible=[]
    for headroom in (0,64):
        possible.append({"facts":[{"entity":"db","variable":"connection_headroom","value":headroom},
                                  *[{"entity":role,"variable":"connections_per_replica","value":2} for role in objects]],
                         "relations":[{"source":role,"target":"db","kind":"competes_for"} for role in objects]})
    probes.append(Probe("shared_dependency","db","connection_headroom","native_graph",possible,cost=2,timeout=10,ttl=120))
    template=copy.deepcopy(objects["new"]["spec"]["template"])
    policy=Policy(ns,["entry","old","new"],
         selector_options={"entry":{"standby":{"selector":{"app":prefix+"-old"},"destination":"old"}}},
         rollback_options={"new":{"known_good":{"template":template,"destination":"approved_new"}}},
         max_replicas={"old":3,"new":3},min_support=5)
    plans=[Plan("no_action",()),Plan("switch_standby",(Action("restore_selector","entry",{"option":"standby"},"old"),)),
           Plan("rollback_active",(Action("rollback_version","new",{"option":"known_good"},"approved_new"),))]
    sources={}
    for role in ("old","new","peer"):
        name=prefix+"-"+role
        sources[role]=transaction(application,endpoint(client,name),application+"-direct-"+role)
    business_name="target"
    business=transaction(application,endpoint(client,business_name),application+"-business")
    peer=copy.deepcopy(sources["peer"]);peer["source_id"]=application+"-non-target"
    bad=copy.deepcopy(template)
    bad["spec"]["containers"][0]["readinessProbe"].update(tcpSocket={"port":1},failureThreshold=1)
    base=[{"entity":"entry","path":"/spec/selector","value":{"app":prefix+"-new"}},
          {"entity":"new","path":"/spec/template","value":template}]
    cases=[{"group_id":"pilot-endpoint-001","condition_id":"selector-drift","comparison_group":"pilot-endpoint-001",
            "diagnosis_label":0,"baseline_changes":base,"fault_changes":[{"entity":"entry","path":"/spec/selector","value":{"app":"unmatched-selector"}}],
            "baseline_settle_seconds":3,"fault_settle_seconds":4},
           {"group_id":"pilot-endpoint-001","condition_id":"readiness-drift","comparison_group":"pilot-endpoint-001",
            "diagnosis_label":1,"baseline_changes":base,"fault_changes":[{"entity":"new","path":"/spec/template","value":bad}],
            "baseline_settle_seconds":3,"fault_settle_seconds":10}]
    config={"schema":"native_intervention_pilot_v2","backend":"native","native_application":application,
            "application":application+"-native-dependency-slice","experimental_namespace":ns,
            "scope":"native application dependency slice; not complete application recovery",
            "kubeconfig":client.context.kubeconfig,"seed":20260929,"repeats":repeats,
            "inventory":{"namespace":ns,"ttl":120,"entities":entities,"goals":["entry"],"relations":relations,"contract_facts":[],"probes":[asdict(p) for p in probes]},
            "policy":asdict(policy),"plans":[p.to_dict() for p in plans],"arms":[p.id for p in plans],
            "initial_probes":["old_health","old_capacity","shared_dependency","old_cpu","peer_cpu"],
            "direct_workloads":sources,"workloads":{"business":business,"peer":peer},"continuous_workload":True,
            "convergence_timeout":240 if application=="trainticket" else 90,"measurement_timeout":90,"reset_health_timeout":180,
            "verification":{"required_windows":3,"min_samples":20,"error_margin":0.02,"latency_multiplier":1.5,
                            "throughput_fraction":0.8,"max_age":180,"require_canary":True},
            "cases":cases,"interpretation":"same coarse endpoint-unavailable symptom; distinct fine mechanisms with truthful diagnostic baseline labels",
            "diagnosis_label_mapping":{"0":"route.selector_drift","1":"active.readiness_drift"},
            "state_target_definition":["log1p_ready_endpoints","log1p_ready_replicas","business_error_fraction","peer_error_fraction","log1p_business_p95_ms"]}
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open("x") as stream:json.dump(config,stream,indent=2);stream.write("\n")
    print(json.dumps({"config":str(out),"application":config["application"],"planned":len(cases)*len(plans)*repeats}))


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--application",choices=["boutique","trainticket"],required=True)
    p.add_argument("--out",type=Path,required=True);p.add_argument("--repeats",type=int,default=5)
    args=p.parse_args();build(args.application,args.out,args.repeats)
