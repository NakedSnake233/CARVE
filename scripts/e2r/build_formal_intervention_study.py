#!/usr/bin/env python3
"""Freeze grouped native interventions after exploratory ranges and SLOs are measured."""
from __future__ import annotations
import argparse
import copy
import json
import math
from pathlib import Path
import random
import statistics
import sys
from dataclasses import asdict
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.schema import Action, Plan
from scripts.e2r.profile_native_intervention import cpu_template,db_binding,configure


def windows(row):return [w['business'] for w in row['windows']]

def latency(row):return statistics.median(w['p95_ms'] for w in windows(row))

def capacity(row):
    observed=row.get(row['point']['route']+'_capacity',{})
    if observed.get('status')!='observed':return None
    value=observed['observation']
    if 'value' in value:return value['value']
    return next((f['value'] for f in value.get('facts',[]) if f['variable']=='capacity'),None)


def response_ok(row,ceiling):
    return all(w['error_rate']<=.02 and w['state_canary_ok'] is True and w['throughput_rps']>=row['point']['load']*.8
               and w['p95_ms']<=ceiling for w in windows(row))


def select_ranges(profile,freeze,required_families=('F1','F2','F3')):
    rows=[json.loads(s) for s in (profile/'profiles.jsonl').read_text().splitlines()]
    rows=[r for r in rows if r['status']=='measured' and r['restore']['status']=='restored' and 'traffic_stop_error' not in r]
    bases={r['point']['load']:r for r in rows if r['point']['kind']=='baseline'}
    floor=freeze['protocol']['latency_floor_ms']
    result={}
    admissible=[]
    for row in rows:
        point=row['point']
        if point['kind']!='old_cpu' or point['load'] not in bases:continue
        cap=capacity(row);ceiling=max(latency(bases[point['load']])*1.5,floor)
        if cap is not None and cap>=point['load'] and all(w['state_canary_ok'] is True and w['error_rate']<=.02 for w in windows(row)):
            admissible.append((latency(row)/ceiling,row))
    if 'F1' in required_families:
        if not admissible:raise ValueError('no measured admitted standby range; extend exploratory profiling')
        ratio,standby=max(admissible,key=lambda item:item[0])
        result['F1']={'load':standby['point']['load'],'old_cpu_factor':standby['point']['factor'],
                      'observed_latency_ratio':ratio,'profile_has_slo_contrast':ratio>1,'source_point':standby['point']['id']}
    active_rows=[r for r in rows if r['point']['kind']=='new_cpu' and r['point']['load'] in bases]
    db_candidates=[r for r in rows if r['point']['kind']=='db_cpu' and not response_ok(r,max(latency(bases[r['point']['load']])*1.5,floor))]
    if 'F2' in required_families:
        if not active_rows:raise ValueError('no measured active/new CPU profile; do not reuse old CPU points for F2')
        if not db_candidates:raise ValueError('no measured DB degradation at registered loads; extend native DB profiling')
        cpu=max(active_rows,key=lambda r:latency(r)/max(latency(bases[r['point']['load']])*1.5,floor))
        db=max(db_candidates,key=latency)
        result['F2']={'load':max(cpu['point']['load'],db['point']['load']),'active_cpu_factor':cpu['point']['factor'],
                      'db_cpu_factor':db['point']['factor'],'source_points':[cpu['point']['id'],db['point']['id']]}
    paired={}
    for row in rows:
        if row['point']['kind']=='topology':
            p=row['point'];paired.setdefault((p['load'],p['peer'],p['factor']),{})[p['binding']]=row
    candidates=[]
    for key,pair in paired.items():
        if set(pair)!={'shared-db','private-db'}:continue
        if any(capacity(r) is None or capacity(r)<key[0] for r in pair.values()):continue
        ceiling=max(latency(pair['private-db'])*1.5,floor)
        if response_ok(pair['private-db'],ceiling):candidates.append((latency(pair['shared-db'])/ceiling,key,pair))
    if 'F3' in required_families:
        if not candidates:raise ValueError('no complete admitted two-DB range; extend topology profiling')
        topology=max(candidates,key=lambda item:item[0])
        result['F3']={'load':topology[1][0],'peer_rps':topology[1][1],'db_cpu_factor':topology[1][2],
                       'observed_latency_ratio':topology[0],'profile_has_slo_contrast':topology[0]>1,
                       'source_points':[r['point']['id'] for r in topology[2].values()]}
    return result


def probe_catalog(config):
    for probe in config['inventory']['probes']:
        if probe['variable']=='capacity':
            probe['kind']='native_graph'
            probe['possible_values']=[{'facts':[{'entity':probe['entity'],'variable':'capacity','value':cap},
                {'entity':probe['entity'],'variable':'latency_ms','value':latency},
                {'entity':probe['entity'],'variable':'error_rate','value':0},
                {'entity':probe['entity'],'variable':'healthy','value':True}]} for cap,latency in ((1,500),(64,5))]
        if probe['id']=='shared_dependency':
            probe['timeout']=20
            values=[]
            for binding in ('db','db_private'):
                for headroom in (0,96):
                    values.append({'facts':[
                        *[{'entity':db,'variable':'connection_headroom','value':headroom} for db in ('db','db_private')],
                        *[{'entity':role,'variable':'connections_per_replica','value':10} for role in ('old','new','peer')],
                        *[{'entity':role,'variable':'dependencies_known','value':True} for role in ('old','new','peer')]],
                        'relations':[{'source':role,'target':db,'kind':'competes_for',
                            'validity':'present' if db==({'old':binding,'new':'db_private','peer':'db'}[role]) else 'absent'}
                            for role in ('old','new','peer') for db in ('db','db_private')]})
            probe['possible_values']=values
    return config


def build(inputs,out):
    declarations=json.loads(inputs.read_text());prepared={}
    # Inspect all exploratory prerequisites before creating a formal run directory.
    for app,entry in declarations.items():
        profile=Path(entry['profile']);summary=json.loads((profile/'summary.json').read_text())
        if summary['status']!='complete':raise ValueError('exploratory profile incomplete: '+app)
        freeze=json.loads(Path(entry['verification']).read_text())
        families=entry.get('families',['F1','F2','F3'])
        prepared[app]={'profile':profile,'freeze':freeze,'families':families,'ranges':select_ranges(profile,freeze,families),
            'templates':json.loads((profile/'base_templates.json').read_text()),
            'config':json.loads((profile/'profile_protocol.json').read_text())['config']}
    out.mkdir(parents=True,exist_ok=False);(out/'configs').mkdir()
    split={'train':[],'validation':[],'test':[]};campaigns=[];configurations=[];total=0
    rng=random.Random(20260929)
    for app,prepared_app in prepared.items():
        original=prepared_app['templates'];base=prepared_app['config'];prefix='cart' if app=='boutique' else 'station'
        for family in prepared_app['families']:
            parameter=prepared_app['ranges'][family]
            order=list(range(8));rng.shuffle(order)
            partitions={i:'train' if j<5 else 'validation' if j==5 else 'test' for j,i in enumerate(order)}
            anchor=order[0]
            for index in range(8):
                group=f'{family}-config-{index:02d}';application=base['application'];partition=partitions[index]
                split[partition].append(application+'/'+group)
                load=parameter['load']*(1 if index%2==0 else 1.25)
                new_factor=(.85,.95,1.05,1.15)[index//2]
                templates=copy.deepcopy(original)
                templates['new']=cpu_template(templates['new'],new_factor)
                peer=load
                if family=='F3':
                    peer=parameter['peer_rps']*(1 if index%2==0 else 1.25)
                    templates['new']=db_binding(templates['new'],app,'private-db')
                    templates['db']=cpu_template(templates['db'],parameter['db_cpu_factor'])
                config=probe_catalog(copy.deepcopy(base))
                # Formal profiles use the persistent second database created by
                # the reviewed storage stage. It is measured but never an
                # unrestricted action target.
                if not any(e.get('id')=='db_private' for e in config['inventory']['entities']):
                    config['inventory']['entities'].append({'id':'db_private','role':'shared_dependency','kind':'Deployment',
                        'namespace':config['experimental_namespace'],'name':'private-db','binding':'independent-storage'})
                config['inventory']['relations']=[r for r in config['inventory']['relations'] if r['kind']!='competes_for']
                config['discover_dependencies']=True
                config.update(schema='native_intervention_formal_v1',stage='formal',family=family,repeats=12 if index==anchor else 5,
                    seed=20260929+index,verification=prepared_app['freeze']['protocol'],
                    monitor_execution_safety=True,direct_baseline_restore=True,retain_baseline_context=True,
                    record_nondegrading_attempts=True,convergence_timeout=180,reset_health_timeout=300,measurement_timeout=120,
                    baseline_consecutive_windows=2,baseline_health_timeout=180,
                    observation_budget={'max_calls':3,'max_cost':6,'max_seconds':40})
                config['policy']['rollback_options']['new']['known_good']['template']=copy.deepcopy(templates['new'])
                config['policy']['allowed_targets']=sorted(set(config['policy']['allowed_targets'])|{'peer','db'})
                config['policy']['max_replicas']={'old':3,'new':3}
                switch=Action('restore_selector','entry',{'option':'standby'},'old')
                plans=[Plan('no_action',())]
                if family in ('F1','F3'):
                    plans += [Plan('switch_standby',(switch,)),
                              Plan('scale_old_then_switch',(Action('scale','old',{'replicas':3}),switch))]
                if family=='F2':
                    plans += [Plan('scale_active',(Action('scale','new',{'replicas':3}),))]
                plans += [Plan('rollback_active',(Action('rollback_version','new',{'option':'known_good'},'approved_new'),))]
                config['plans']=[p.to_dict() for p in plans];config['arms']=[p.id for p in plans]
                for workload in config['workloads'].values():workload['workers']=256
                for workload in config['direct_workloads'].values():workload['workers']=256
                cases=[]
                conditions=('standby_ample','standby_restricted') if family=='F1' else ('active_cpu','database_cpu') if family=='F2' else ('separate','shared')
                for condition in conditions:
                    baseline=copy.deepcopy(templates)
                    if family=='F1' and condition=='standby_restricted':baseline['old']=cpu_template(baseline['old'],parameter['old_cpu_factor'])
                    if family=='F3':baseline['old']=db_binding(baseline['old'],app,'private-db' if condition=='separate' else 'shared-db')
                    changes=[{'entity':entity,'path':'/spec/template','value':template} for entity,template in baseline.items()]
                    changes += [{'entity':role,'path':'/spec/replicas','value':1} for role in ('old','new','peer')]
                    changes += [{'entity':'entry','path':'/spec/selector','value':{'app':prefix+'-new'}}]
                    if family=='F2':
                        entity='new' if condition=='active_cpu' else 'db'
                        factor=parameter['active_cpu_factor'] if entity=='new' else parameter['db_cpu_factor']
                        fault=[{'entity':entity,'path':'/spec/template','value':cpu_template(baseline[entity],factor)}]
                        label=2 if entity=='new' else 3;wait=[entity]
                    else:
                        broken=copy.deepcopy(baseline['new']);broken['spec']['containers'][0]['readinessProbe'].update(tcpSocket={'port':1},failureThreshold=1)
                        fault=[{'entity':'new','path':'/spec/template','value':broken}];label=1;wait=[]
                    cases.append({'group_id':group,'condition_id':condition,'comparison_group':group,'diagnosis_label':label,
                        'offered_rps':load,'peer_rps':peer,'baseline_changes':changes,'fault_changes':fault,'fault_wait_entities':wait,
                        'baseline_settle_seconds':3,'fault_settle_seconds':10,
                        'fault_wait_entities':(['new'] if family in ('F1','F3') else wait)})
                config['cases']=cases
                config['initial_probes']=['old_health','old_capacity','new_capacity','shared_dependency','old_cpu','peer_cpu']
                config['diagnosis_label_mapping']={'1':'active.readiness_drift','2':'active.cpu_quota_drift','3':'shared_db.cpu_quota_drift'}
                config['interpretation']='prospective grouped native study; actual outcomes and ineligible perturbations remain measured, never presumed'
                identity=app+'-'+group;file=out/'configs'/(identity+'.json');file.write_text(json.dumps(config,indent=2)+'\n')
                path=out/'campaigns'/identity
                campaigns.append({'id':identity,'path':str(path.resolve()),'config':str(file.resolve()),'partition':partition})
                configurations.append({'id':identity,'family':family,'partition':partition,'load':load,'peer_rps':peer,
                    'new_cpu_factor':new_factor,'repeats':config['repeats'],'profile_parameters':parameter})
                total+=len(cases)*len(plans)*config['repeats']
    manifest={'schema':'native_formal_collection_manifest_v1','campaigns':campaigns,'split':split}
    (out/'collection_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (out/'PREREGISTRATION.json').write_text(json.dumps({'schema':'native_formal_preregistration_v1','status':'frozen_before_formal_data',
        'source_plan':'enhancement/improvement.md','configuration_groups':len(configurations),'planned_interventions':total,
        'fixed_action_catalog':['no_action','switch_standby','scale_old_then_switch','scale_active','rollback_active'],
        'training_equivalence_anchors':'one randomly assigned training group per application/family has 12 repeats; others have 5',
        'seeds':[42,43,44],'hidden':64,'epochs':40,'learning_rate':.003,'distinguish_weight':.2,'compact_weight':.01,
        'minimum_repeats':5,'split':split,'configurations':configurations,
        'profile_source':declarations,'limitations':['A profiled latency contrast does not guarantee a formal effect difference.',
            'Nondegrading perturbations retain their attempt denominator and have unknown repair outcomes.',
            'No result or novelty claim is preregistered as already achieved.']},indent=2)+'\n')
    print(json.dumps({'formal_directory':str(out),'groups':len(campaigns),'planned_interventions':total}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--inputs',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();build(a.inputs,a.out)
