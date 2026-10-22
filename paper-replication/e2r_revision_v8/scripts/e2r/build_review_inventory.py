#!/usr/bin/env python3
"""Build reviewer-facing dataset, workload, graph-size and fairness inventories."""
from pathlib import Path
from collections import Counter, defaultdict
import argparse
import json
import statistics
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from methods.E2R.graph import graphs_for,tensors,use_upstream
from methods.E2R.paths import default_dyrca_root,observation_file


def stats(values):
    return {'min':min(values),'median':statistics.median(values),'max':max(values),'mean':sum(values)/len(values)}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--manifest',default=str(ROOT/'experiments/e2r/runs/full_data_v2/manifest.json'))
    ap.add_argument('--split',default=str(ROOT/'experiments/e2r/runs/gpu_full_4090d/split.json'))
    ap.add_argument('--out-dir',required=True)
    args=ap.parse_args();out=Path(args.out_dir)
    if out.exists():raise SystemExit('Refusing to overwrite inventory output')
    use_upstream(default_dyrca_root())
    manifest_path=Path(args.manifest).resolve();manifest=json.loads(manifest_path.read_text())
    split=json.loads(Path(args.split).read_text());split_of={case:k for k,ids in split.items() for case in ids}
    counts={name:Counter() for name in ('app','scenario','category','service','app_scenario','app_category')}
    candidates=[];telemetry=[];rows=[];sizes=defaultdict(lambda:defaultdict(list));params=defaultdict(Counter)
    for row in manifest['cases']:
        obs=json.loads(observation_file(row,manifest_path).read_text());graphs,_,_=graphs_for(obs)
        counts['app'][row['app']]+=1;counts['scenario'][row['scenario']]+=1
        counts['category'][row['true_category']]+=1;counts['service'][row['true_service']]+=1
        counts['app_scenario'][f"{row['app']}:{row['scenario']}"]+=1
        counts['app_category'][f"{row['app']}:{row['true_category']}"]+=1
        candidates.append(len(obs['candidates']))
        accepted=sum(obs['coverage'].get(k,0) for k in ('metric_accepted','trace_accepted','log_accepted'))
        telemetry.append(accepted)
        metadata=json.loads((Path(row['directory'])/'metadata.json').read_text())
        for key,value in sorted((metadata.get('fault_params') or {}).items()):
            params[f"{row['app']}:{row['scenario']}:{key}"][json.dumps(value,sort_keys=True)]+=1
        graph_sizes={}
        for variant,graph in graphs.items():
            item=tensors(graph,obs);n=len(item['node_ids']);e=len(item['edge_weight'])
            sizes[row['app']][variant+'_nodes'].append(n);sizes[row['app']][variant+'_directed_edges'].append(e)
            graph_sizes[variant]={'nodes':n,'directed_edges':e}
        rows.append({'case_id':row['case_id'],'app':row['app'],'scenario':row['scenario'],
                     'category':row['true_category'],'service':row['true_service'],'split':split_of[row['case_id']],
                     'candidates':len(obs['candidates']),'accepted_telemetry_records':accepted,
                     'graph_sizes':graph_sizes})
    fairness={
      'shared_across_graph_arms':['diagnostic cutoff','observation snapshot','candidate services','base feature function','edge weight=1','split','per-seed frozen scorer'],
      'intentional_differences':{'reference':'pre-event graph','runtime':'diagnosis-time observed graph','rule_patch':'validated supported operations applied to reference'},
      'ground_truth_use':['pre-inference candidate-presence eligibility check','recorded fault_end_time cutoff validity check','split','supervised loss','evaluation'],
      'forbidden_model_inputs':['target/scenario/fault params','original incident name','ground truth','post-recovery state','old cached plan'],
      'external_baseline_adapter_requirements':['same snapshot facts','same candidate list','same held-out IDs','same LLM alias/temperature/request budget when applicable','same failure denominator','same mutation namespace and safety policy for recovery']}
    summary={'schema':'e2r_review_inventory_v1','cases':len(rows),
             'counts':{k:dict(sorted(v.items())) for k,v in counts.items()},
             'candidate_count':stats(candidates),'accepted_telemetry_records':stats(telemetry),
             'topology_only_cases':sum(v==0 for v in telemetry),
             'graph_size':{app:{key:stats(vals) for key,vals in values.items()} for app,values in sizes.items()},
             'fault_parameters':{key:dict(value) for key,value in sorted(params.items())},
             'workload':{
                'recorded_798_campaign':{'boutique':'not encoded in incident metadata','trainticket':'not encoded in incident metadata'},
                'planned_isolated_live_boutique':{'loadgenerator_users':10,'image_tag':'v0.10.1','source':'experiments/e2r/isolated_boutique.yaml'},
                'interpretation':'Unknown historical workload is reported as unknown; it is not inferred from telemetry.'},
             'fairness_contract':fairness}
    out.mkdir(parents=True)
    (out/'inventory.json').write_text(json.dumps(summary,indent=2)+'\n')
    with (out/'cases.jsonl').open('w') as f:
        for row in rows:f.write(json.dumps(row)+'\n')
    lines=['# E2R 审稿复核数据与公平性清单','',
           f"- 当前 topology campaign：{len(rows)} 个案例；与历史 760 条七故障记录分开。",
           f"- 应用：{dict(counts['app'])}；scenario：{dict(counts['scenario'])}。",
           f"- candidate 数：{summary['candidate_count']}。",
           f"- 无诊断期 telemetry 记录：{summary['topology_only_cases']}（当前均来自 TrainTicket）。",
           '- 原始 798 campaign 的请求负载未写入 incident metadata，因此记为 unknown。',
           '- 计划中的 isolated Boutique workload 固定 `USERS=10`、镜像 `v0.10.1`；真实运行仍需保存 warm-up、throughput、error 与 p95。','',
           '## 公平性契约','']
    for key,value in fairness.items():lines.append(f'- **{key}**: {json.dumps(value,ensure_ascii=False)}')
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({k:summary[k] for k in ('cases','candidate_count','topology_only_cases','graph_size')},indent=2))


if __name__=='__main__':main()
