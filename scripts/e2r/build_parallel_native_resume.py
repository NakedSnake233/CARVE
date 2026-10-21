#!/usr/bin/env python3
"""Amend existing formal slots into balanced, namespace-isolated collection lanes."""
from __future__ import annotations
import argparse
import copy
import json
import random
import re
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.platform import KubectlClient

ORIGINAL={'boutique':'formal_boutique_v4','trainticket':'formal_trainticket_v2'}


def remap(value,source,target,ips):
    if isinstance(value,dict):return {k:remap(v,source,target,ips) for k,v in value.items()}
    if isinstance(value,list):return [remap(v,source,target,ips) for v in value]
    if not isinstance(value,str):return value
    value=value.replace(source,target)
    for old,new in ips.items():value=re.sub(r'(?<![\d.])'+re.escape(old)+r'(?![\d.])',new,value)
    return value


def schedule(config):
    jobs=[{'group_id':c['group_id'],'condition_id':c['condition_id'],'repeat':r,'arm':a}
          for c in config['cases'] for r in range(config['repeats']) for a in config['arms']]
    random.Random(config['seed']).shuffle(jobs)
    return jobs


def remaining(config,original,amendments=()):
    order=json.loads((original/'order.json').read_text()) if (original/'order.json').exists() else schedule(config)
    covered=set()
    sources=[Path(original),*map(Path,amendments)]
    if len(set(sources))!=len(sources):raise ValueError('duplicate attempt source directory')
    for source_index,source in enumerate(sources):
        if not (source/'episodes.jsonl').exists():continue
        assigned=json.loads((source/'protocol.json').read_text())['scheduled_jobs'] if source_index else None
        for i,line in enumerate((source/'episodes.jsonl').read_text().splitlines()):
            row=json.loads(line)
            slot=i if assigned is None else assigned[i]['original_schedule_slot']
            if type(slot) is not int or not 0<=slot<len(order):raise ValueError('invalid original slot')
            if assigned is not None and row.get('original_schedule_slot')!=slot:
                raise ValueError('missing or inconsistent original slot provenance')
            if slot in covered:raise ValueError('attempt reruns an already covered slot')
            expected=order[slot]
            for key,other in [('group_id','group_id'),('condition_id','condition_id'),('repeat','repeat'),('chosen_plan','arm')]:
                if row[key]!=expected[other]:raise ValueError('original schedule/record mismatch')
                if assigned is not None and assigned[i][other]!=expected[other]:raise ValueError('amended assignment differs from original schedule')
            if row['status'] not in ('failed','started') and row['restore']['status']=='restored':covered.add(slot)
    return [{**j,'original_schedule_slot':i} for i,j in enumerate(order) if i not in covered],len(order),len(covered)


def attach_profile_background(config,app,family,registration):
    if app!='boutique' or family not in ('F2','F3'):return
    parameter=next(c['profile_parameters'] for c in registration['configurations'] if c['family']==family)
    profile=ROOT/registration['profile_source'][app]['profile']
    points=[json.loads(s)['point'] for s in (profile/'profiles.jsonl').read_text().splitlines()]
    selected=[p['background'] for p in points if p['id'] in parameter['source_points'] and p.get('background')]
    if not selected or any(s!=selected[0] for s in selected):raise ValueError('profile background is missing/inconsistent')
    config['background_workloads']={'background':copy.deepcopy(selected[0])}
    if not any(e['id']=='background' for e in config['inventory']['entities']):
        config['inventory']['entities'].append({'id':'background','role':'factor','binding':'registered-cache-background'})
    if not any(r['source']=='background' for r in config['inventory']['relations']):
        config['inventory']['relations'].append({'source':'background','target':'db','kind':'competes_for',
            'observed_at':time.time(),'ttl':1209600,'origin':'configuration'})
    config['background_amendment']='Carry the selected exploratory SET/GET load into the as-yet-unattempted formal family; preserve its rate/payload. This is offered competitor load, not an additional business endpoint.'


def build(out,runtime,lanes,mode,sanity=None):
    if mode=='formal':
        if not sanity:raise ValueError('completed concurrent sanity manifest required')
        sm=json.loads(sanity.read_text())
        if len(sm['jobs'])!=lanes*2:raise ValueError('sanity does not cover every lane')
        for j in sm['jobs']:
            if json.loads(Path(j['outputs'][0]['path']).read_text())['status']!='complete':
                raise ValueError('concurrent sanity has not passed: '+j['id'])
    out.mkdir(parents=True,exist_ok=False);(out/'configs').mkdir()
    jobs=[];assignments=[];split={'train':[],'validation':[],'test':[]};originals=[]
    for app in ('boutique','trainticket'):
        folder=ROOT/'enhancement/formal'/(app+'_v1')
        collection=json.loads((folder/'collection_manifest.json').read_text())
        registration=json.loads((folder/'PREREGISTRATION.json').read_text())
        for key in split:split[key]+=collection['split'][key]
        source='e2r-isg-'+app;client=KubectlClient(source)
        services=client.list('Service')
        lane_data={};loads={i:0 for i in range(1,lanes+1)}
        for lane in loads:
            namespace=source if lane==1 else source+'-p'+str(lane)
            replica=KubectlClient(namespace)
            lane_data[lane]=(namespace,{s['spec']['clusterIP']:replica.get('Service',s['metadata']['name'])['spec']['clusterIP'] for s in services})
        entries=collection['campaigns']
        if mode=='sanity':
            entries=[]
            for lane in loads:
                family='F1' if lane<=2 else 'F2' if app=='boutique' and lane==3 else 'F3'
                identity=app+'-'+family+'-config-01'
                entry=next(x for x in collection['campaigns'] if x['id']==identity)
                entries.append({**entry,'lane':lane})
        for entry in entries:
            config=json.loads(Path(entry['config']).read_text());identity=entry['id']
            original=ROOT/'enhancement/runs'/ORIGINAL[app]/identity
            pending,planned,covered=remaining(config,original)
            if mode=='formal':
                originals.append({'id':identity,'directory':str(original),'planned_slots':planned,'covered_original_slots':covered})
                if not pending:continue
                lane=1 if original.exists() else min(loads,key=loads.get)
                loads[lane]+=len(pending)
            else:
                lane=entry['lane'];pending=[]
                for case,arm in zip(config['cases'],[('scale_active' if config['family']=='F2' else 'scale_old_then_switch'),'rollback_active']):
                    slot=next(i for i,j in enumerate(schedule(config)) if j['condition_id']==case['condition_id'] and j['arm']==arm and j['repeat']==0)
                    pending.append({**schedule(config)[slot],'original_schedule_slot':slot})
                identity=app+'-p'+str(lane)+'-'+config['family']+'-sanity'
            attach_profile_background(config,app,config['family'],registration)
            namespace,ips=lane_data[lane]
            config=remap(config,source,namespace,ips)
            config.update(stage='formal' if mode=='formal' else 'parallel_sanity',traffic_process=True,
                scheduled_jobs=pending,resource_lock_directory=str(ROOT/'enhancement/namespace_locks'),
                amendment={'date':'2026-09-30','original_config':entry['config'],'original_campaign':str(original),
                           'lane':lane,'namespace':namespace,'scope':'same slots, frozen outcomes, separate traffic process and owned namespace replica'})
            path=out/'configs'/(identity+'.json');path.write_text(json.dumps(config,indent=2)+'\n')
            result=out/'campaigns'/identity
            jobs.append({'id':identity,'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/run_native_campaign.py'),
                         '--config',str(path),'--out',str(result)],'resources':['namespace:'+namespace],
                         'requires':[str(runtime/'RUNTIME.json')],
                         'outputs':[{'path':str(result/'summary.json'),'json_equals':{'status':'complete'}}]})
            assignments.append({'id':identity,'original_id':entry['id'],'namespace':namespace,'lane':lane,
                                'application':app,'family':config['family'],'partition':entry['partition'],
                                'attempts':len(pending),'config':str(path),'path':str(result)})
    manifest={'schema':'local_experiment_queue_manifest_v1','cwd':str(ROOT),'max_parallel':lanes*2,'jobs':jobs}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (out/'AMENDMENT.json').write_text(json.dumps({'mode':mode,'original_campaigns':originals,'assignments':assignments,
        'split':split,'attempts_scheduled':sum(a['attempts'] for a in assignments),
        'original_attempts_preserved':True,'original_failed_slots_replaced_without_removing_failures':True,
        'all_background_profiles_explicitly_carried_into_unattempted_families':True},indent=2)+'\n')
    print(json.dumps({'manifest':str(out/'manifest.json'),'campaigns':len(jobs),'attempts':sum(a['attempts'] for a in assignments)}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--runtime',type=Path,required=True);p.add_argument('--lanes',type=int,choices=[1,2,4],required=True)
    p.add_argument('--mode',choices=['sanity','formal'],required=True);p.add_argument('--sanity',type=Path)
    a=p.parse_args();build(a.out.resolve(),a.runtime.resolve(),a.lanes,a.mode,a.sanity)
