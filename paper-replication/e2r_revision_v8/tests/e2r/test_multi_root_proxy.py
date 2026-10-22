from methods.E2R.graph import operation_catalog
from scripts.e2r.run_multi_root_proxy import make_pairs,merge_observations,average_precision


def observation(case_id,root_value):
    return {'case_id':case_id,'candidates':['a','b','c'],
        'pre':{'timestamp':'x','pods':[{'name':'a-p','service':'a','ip':'10.0.0.1'},
                                      {'name':'b-p','service':'b','ip':'10.0.0.2'}],
               'deployments':[{'name':'a','readyReplicas':1},{'name':'b','readyReplicas':1}],
               'services':[{'name':'a','endpoint_ips':['10.0.0.1']},
                           {'name':'b','endpoint_ips':['10.0.0.2']}]},
        'during':{'timestamp':'y','pods':[{'name':'a-p','service':'a','ip':'10.0.0.1'},
                                         {'name':'b-p','service':'b','ip':'10.0.0.2'}],
                  'deployments':[{'name':'a','readyReplicas':root_value},{'name':'b','readyReplicas':root_value}],
                  'services':[{'name':'a','endpoint_ips':['10.0.0.1']},
                              {'name':'b','endpoint_ips':['10.0.0.2']}]},
        'observed_metrics':{'a':{'during:cpu_usage':{'mean':root_value}},
                            'b':{'during:cpu_usage':{'mean':root_value}}},
        'call_edges':{'pre':[],'during':[{'source':'a','target':'b','count':root_value}]},
        'window':{},'coverage':{'metric_accepted':1}}


def test_merge_overlays_only_second_root_and_never_embeds_labels():
    base=observation('left',1);overlay=observation('right',9)
    merged=merge_observations(base,overlay,'b','pair:0')
    assert merged['observed_metrics']['a']['during:cpu_usage']['mean']==1
    assert merged['observed_metrics']['b']['during:cpu_usage']['mean']==9
    assert next(row for row in merged['during']['deployments'] if row['name']=='b')['readyReplicas']==9
    assert 'true_service' not in merged and 'roots' not in merged
    for phase in ('pre','during'):
        ips=[pod['ip'] for pod in merged[phase]['pods'] if pod.get('ip')]
        assert len(ips)==len(set(ips))
        assert all(ip in set(ips) for service in merged[phase]['services']
                   for ip in service['endpoint_ips'])
        assert merged[phase]['timestamp'].startswith('synthetic:')
    assert merged['call_edges']['during']==[{'source':'a','target':'b','count':9}]
    assert merged['window']['time_policy']=='no_shared_physical_clock_source_local_windows_only'
    assert merged['synthetic_composition']['construction_uses_evaluator_root_to_select_overlay_service']


def test_pairing_uses_each_case_at_most_once_and_distinct_roots():
    rows=[{'case_id':f'c{i}','app':'x','scenario':f'T{i%2}',
           'true_service':f's{i%3}'} for i in range(9)]
    pairs,leftovers=make_pairs(rows,7)
    ids=[row['case_id'] for pair in pairs for row in pair]
    assert len(ids)==len(set(ids))
    assert all(left['true_service']!=right['true_service'] for left,right in pairs)
    assert len(ids)+len(leftovers)==len(rows)
    assert average_precision(['a','x','b'],['a','b'])==(1+2/3)/2


def test_unchanged_overlay_pod_identity_is_stable_across_phases():
    base=observation('left',1);overlay=observation('right',9)
    base['call_edges']['pre']=base['call_edges']['during']
    overlay['call_edges']['pre']=overlay['call_edges']['during']
    merged=merge_observations(base,overlay,'b','stable-pair')
    pre=next(p for p in merged['pre']['pods'] if p.get('service')=='b')
    during=next(p for p in merged['during']['pods'] if p.get('service')=='b')
    assert (pre['name'],pre['ip'])==(during['name'],during['ip'])
    catalog=operation_catalog(merged)
    assert not any('__mr_' in json_text for entry in catalog
                   for json_text in [__import__('json').dumps(entry)])
