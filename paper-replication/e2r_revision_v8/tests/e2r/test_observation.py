import json
from pathlib import Path

import pytest

from methods.E2R.data import build_observation,timestamp
from methods.E2R.paths import default_dyrca_root
from methods.E2R.graph import use_upstream,graphs_for,select_plan,operation_catalog,tensors,observation_graph
from scripts.e2r.run_graph_experiment import explicit_split,split_cases
from scripts.e2r.run_operation_ablation import selected_ids


def write_fixture(tmp_path):
    pre={'timestamp':'2026-01-01T00:00:00Z',
         'pods':[{'name':'a-old','service':'a','ip':'10.0.0.1','version':'v1'},
                 {'name':'b-old','service':'b','ip':'10.0.0.2','version':'v1'}],
         'deployments':[{'name':s,'replicas':1,'readyReplicas':1} for s in ['a','b']],
         'services':[{'name':'a','endpoint_ips':['10.0.0.1']},{'name':'b','endpoint_ips':['10.0.0.2']}]}
    during=json.loads(json.dumps(pre));during['timestamp']='2026-01-01T00:01:00Z';during['services'][0]['endpoint_ips']=[]
    (tmp_path/'topology').mkdir();(tmp_path/'metrics').mkdir();(tmp_path/'traces').mkdir();(tmp_path/'logs').mkdir()
    for name,data in [('pre',pre),('during',during)]:
        (tmp_path/f'topology/{name}.json').write_text(json.dumps(data))
    (tmp_path/'metadata.json').write_text(json.dumps({'target_service':'a','scenario_id':'T3','fault_params':{'truth':'a'}}))
    (tmp_path/'ground_truth.json').write_text(json.dumps({'root_cause_service':'a'}))
    (tmp_path/'metrics/cpu_usage.csv').write_text('timestamp,metric_name,labels_json,value\n2026-01-01T00:00:30Z,cpu_usage,"{""pod"":""a-old""}",2\n2026-01-01T00:02:00Z,cpu_usage,"{""pod"":""a-old""}",999\n')
    return tmp_path


def test_labels_and_post_state_do_not_change_observations(tmp_path):
    root=write_fixture(tmp_path);before=build_observation(root,'case00001')
    (root/'metadata.json').write_text(json.dumps({'target_service':'b','scenario_id':'T4','fault_params':{'truth':'b'}}))
    (root/'ground_truth.json').write_text(json.dumps({'root_cause_service':'b'}))
    (root/'topology/post.json').write_text(json.dumps({'root_cause_service':'b','future':'completely changed'}))
    assert before==build_observation(root,'case00001')
    assert before['observed_metrics']['a']['during:cpu_usage']['max']==2
    assert before['coverage']['metric_future_or_outside']==1
    assert before['window']['max_used_timestamp']<=before['window']['cutoff']


def test_empty_endpoint_patch_and_inactive_edges(tmp_path):
    use_upstream(default_dyrca_root())
    obs=build_observation(write_fixture(tmp_path),'case00001')
    variants,catalog,plan=graphs_for(obs)
    assert any(c['operation']['op']=='ADD_BLACKHOLE_NODE' for c in catalog)
    material=tensors(variants['patch'],obs)
    ids=material['node_ids'];pairs=set(map(tuple,material['edge_index'].t().tolist()))
    assert (ids.index('a'),ids.index('pod:a-old')) not in pairs
    assert (ids.index('a'),ids.index('blackhole:a')) in pairs
    assert material['candidate_services']==obs['candidates']
    # Base observation features for common services identical across all arms.
    other=tensors(variants['reference'],obs)
    assert material['x'][ids.index('a')].tolist()==other['x'][other['node_ids'].index('a')].tolist()
    with pytest.raises(ValueError,match='unsupported'):
        select_plan(obs,catalog,['oracle-root'])


def test_operation_deletion_removes_dependent_edges(tmp_path):
    use_upstream(default_dyrca_root());obs=build_observation(write_fixture(tmp_path),'case00001')
    catalog=operation_catalog(obs)
    creators=[row for row in catalog if row['operation']['op']=='ADD_BLACKHOLE_NODE']
    assert creators
    kept=selected_ids(catalog,{'ADD_BLACKHOLE_NODE'})
    assert creators[0]['id'] not in kept
    assert all(set(row.get('requires',[])).issubset(kept) for row in catalog if row['id'] in kept)


def test_fixed_split_disjoint_and_no_tiny_split_fallback():
    cases=[{'case_id':f'c{i}','app':'a','scenario':'s'} for i in range(20)]
    split=split_cases(cases)
    ids=[{r['case_id'] for r in split[k]} for k in ['train','val','test']]
    assert not(ids[0]&ids[1] or ids[0]&ids[2] or ids[1]&ids[2])
    assert set.union(*ids)=={r['case_id'] for r in cases}
    with pytest.raises(ValueError,match='>=3'):split_cases(cases[:2])


def test_explicit_split_preserves_declared_order_and_rejects_overlap(tmp_path):
    cases=[{'case_id':f'c{i}'} for i in range(6)]
    path=tmp_path/'split.json';path.write_text(json.dumps({'train':['c2','c0'],'val':['c3'],'test':['c5']}))
    split,selected=explicit_split(cases,path)
    assert [r['case_id'] for r in split['train']]==['c2','c0']
    assert {r['case_id'] for r in selected}=={'c0','c2','c3','c5'}
    path.write_text(json.dumps({'train':['c0'],'val':['c0'],'test':['c5']}))
    with pytest.raises(ValueError,match='overlap'):explicit_split(cases,path)


def test_timestamp_respects_offset():
    assert timestamp('2026-01-01T00:00:00-05:00')==timestamp('2026-01-01T05:00:00Z')


def test_unchanged_alias_not_reported_but_new_redirect_is(tmp_path):
    use_upstream(default_dyrca_root())
    root=write_fixture(tmp_path)
    for phase in ['pre','during']:
        path=root/f'topology/{phase}.json';topo=json.loads(path.read_text())
        topo['services'].append({'name':'public-a','endpoint_ips':['10.0.0.1']})
        if phase=='during':topo['services'][1]['endpoint_ips']=['10.0.0.1']
        path.write_text(json.dumps(topo))
    ops=operation_catalog(build_observation(root,'case00001'))
    mismatches=[x['operation'] for x in ops if x['operation']['op']=='ADD_TRACE_MISMATCH_EDGE']
    assert [(o['source'],o['target']) for o in mismatches]==[('b','a')]


def test_replica_metric_uses_deployment_not_exporter(tmp_path):
    root=write_fixture(tmp_path)
    (root/'metrics/replica_count.csv').write_text('timestamp,metric_name,labels_json,value\n2026-01-01T00:00:30Z,replica_count,"{""service"":""prom-kube-state-metrics"",""deployment"":""a""}",3\n')
    obs=build_observation(root,'case00001')
    assert obs['observed_metrics']['a']['during:replica_count']['mean']==3
