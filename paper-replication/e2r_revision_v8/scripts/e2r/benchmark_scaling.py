#!/usr/bin/env python3
"""Measure E2R preprocessing/inference scaling on replicated graph fixtures."""
from pathlib import Path
from collections import defaultdict
import argparse,copy,json,statistics,sys,time

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.graph import graphs_for,model_input,operation_catalog,tensors,use_upstream
from methods.E2R.paths import default_dyrca_root,observation_file
from scripts.e2r.run_graph_experiment import move_item,resolve_device,synchronize


def replicate(obs,copies,telemetry_factor):
    result={'case_id':f"{obs['case_id']}-rep{copies}-tele{telemetry_factor}",'candidates':[],
            'pre':{'timestamp':obs['pre']['timestamp'],'pods':[],'deployments':[],'services':[]},
            'during':{'timestamp':obs['during']['timestamp'],'pods':[],'deployments':[],'services':[]},
            'observed_metrics':{},'call_edges':{'pre':[],'during':[]},
            'window':copy.deepcopy(obs['window']),'coverage':copy.deepcopy(obs['coverage'])}
    base=set(obs['candidates'])
    for copy_id in range(copies):
        names={s:f'{s}__r{copy_id}' for s in obs['candidates']};result['candidates']+=list(names.values())
        for phase in ('pre','during'):
            ipmap={}
            for pod in obs[phase]['pods']:
                service=pod.get('service')
                if service not in base:continue
                value=copy.deepcopy(pod);value['name']=f"{pod['name']}__r{copy_id}";value['service']=names[service]
                if pod.get('ip'):ipmap[pod['ip']]=f"r{copy_id}:{pod['ip']}";value['ip']=ipmap[pod['ip']]
                result[phase]['pods'].append(value)
            for dep in obs[phase]['deployments']:
                if dep.get('name') not in base:continue
                value=copy.deepcopy(dep);value['name']=names[dep['name']];result[phase]['deployments'].append(value)
            for svc in obs[phase]['services']:
                if svc.get('name') not in base:continue
                value=copy.deepcopy(svc);value['name']=names[svc['name']]
                value['endpoint_ips']=[ipmap[ip] for ip in svc.get('endpoint_ips',[]) if ip in ipmap]
                result[phase]['services'].append(value)
            for edge in obs['call_edges'][phase]:
                if edge['source'] in base and edge['target'] in base:
                    value=copy.deepcopy(edge);value['source']=names[edge['source']];value['target']=names[edge['target']]
                    result['call_edges'][phase].append(value)
        for service,items in obs['observed_metrics'].items():
            if service not in base:continue
            expanded=copy.deepcopy(items)
            originals=list(items.items())
            for factor in range(1,telemetry_factor):
                for key,value in originals:expanded[f'scale_extra_{factor}:{key}']=copy.deepcopy(value)
            result['observed_metrics'][names[service]]=expanded
    for key in result['coverage']:result['coverage'][key]*=copies*telemetry_factor
    return result


def percentile(values,q):
    ordered=sorted(values);return ordered[min(len(ordered)-1,int((len(ordered)-1)*q))]


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--manifest',required=True);ap.add_argument('--checkpoints',required=True)
    ap.add_argument('--out-dir',required=True);ap.add_argument('--base-case',default='case00001');ap.add_argument('--repetitions',type=int,default=10)
    ap.add_argument('--device',default='cpu');ap.add_argument('--dyrca-root',default=str(default_dyrca_root()));args=ap.parse_args();use_upstream(args.dyrca_root)
    import torch
    from dyrca_rca.models.graphsage import WeightedGraphSAGE
    device=resolve_device(torch,args.device)
    if device.type=='cuda':torch.cuda.set_device(device);torch.cuda.reset_peak_memory_stats(device)
    manifest_path=Path(args.manifest).resolve();manifest=json.loads(manifest_path.read_text());row=next(r for r in manifest['cases'] if r['case_id']==args.base_case)
    base=json.loads(observation_file(row,manifest_path).read_text());state=torch.load(Path(args.checkpoints)/'model_seed42.pt',map_location='cpu',weights_only=True)
    model=WeightedGraphSAGE(state['in_dim'],128,2,.1);model.load_state_dict(state['state_dict']);model.to(device);model.eval();raw=[]
    with torch.no_grad():
      for copies in (1,5,10,20,40):
        for volume in (1,2,4,8):
            obs=replicate(base,copies,volume)
            for rep in range(args.repetitions+1):
                t=time.perf_counter();catalog=operation_catalog(obs);catalog_s=time.perf_counter()-t
                t=time.perf_counter();payload=json.dumps(model_input(obs,catalog),sort_keys=True,separators=(',',':'));serialize_s=time.perf_counter()-t
                t=time.perf_counter();graph=graphs_for(obs)[0]['patch'];execute_s=time.perf_counter()-t
                t=time.perf_counter();item=move_item(tensors(graph,obs),device);tensor_s=time.perf_counter()-t
                synchronize(torch,device);t=time.perf_counter();score=model(item['x'],item['edge_index'],edge_weight=item['edge_weight']);score.sum().item();synchronize(torch,device);infer_s=time.perf_counter()-t
                if rep:
                    raw.append({'candidate_services':len(obs['candidates']),'telemetry_factor':volume,'repetition':rep,
                                'nodes':len(item['node_ids']),'directed_edges':len(item['edge_weight']),'serialized_bytes':len(payload.encode()),
                                'catalog_operations':len(catalog),'catalog_seconds':catalog_s,'serialization_seconds':serialize_s,
                                'execution_seconds':execute_s,'tensor_seconds':tensor_s,'inference_seconds':infer_s})
    summary=[]
    for candidates in (13,65,130,260,520):
      for volume in (1,2,4,8):
        group=[r for r in raw if r['candidate_services']==candidates and r['telemetry_factor']==volume]
        row={'candidate_services':candidates,'telemetry_factor':volume,'repetitions':len(group),
             'nodes':group[0]['nodes'],'directed_edges':group[0]['directed_edges'],'serialized_bytes':group[0]['serialized_bytes']}
        for key in ('catalog_seconds','serialization_seconds','execution_seconds','tensor_seconds','inference_seconds'):
            values=[r[key] for r in group];row[key+'_median']=statistics.median(values);row[key+'_p95']=percentile(values,.95)
        summary.append(row)
    out=Path(args.out_dir)
    if out.exists():raise SystemExit('Refusing overwrite')
    out.mkdir(parents=True)
    with (out/'raw.jsonl').open('w') as f:
        for row in raw:f.write(json.dumps(row)+'\n')
    result={'status':'completed_synthetic_scaling_overhead','evaluation_type':'simulation_only','device':str(device),
            'torch':torch.__version__,'base_case':args.base_case,'summary':summary,
            'peak_allocated_bytes':torch.cuda.max_memory_allocated(device) if device.type=='cuda' else None,
            'limits':['Replicated graph fixtures measure overhead only, not accuracy, recovery, production topology or independent telemetry information.',
                      'Extra telemetry keys increase serialization volume but are intentionally ignored by the fixed feature extractor.']}
    (out/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'conditions':len(summary),'device':str(device)},indent=2))


if __name__=='__main__':main()
