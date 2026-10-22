#!/usr/bin/env python3
"""Preserve complete prospective native campaigns and all failed retry ancestry."""
from collections import Counter,defaultdict
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.data import validate_record
from methods.E2R.intervention.model import plan_signature
from methods.E2R.intervention.protocols import canonical,protocol_descriptor,stratum_id
from methods.E2R.intervention.schema import EvidenceGraph,Entity,Plan
from scripts.e2r.terminal_recovery import validate_terminal_recovery


def read(path):return json.loads(Path(path).read_text())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,value):Path(path).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')

def command_paths(job):
    command=job['command'];script=Path(command[2]).resolve()
    if script.name!='run_native_campaign.py':raise ValueError('not a registered native campaign job')
    return Path(command[command.index('--config')+1]),Path(command[command.index('--out')+1]),script.parents[2]


def expected_slots(config):
    if not config.get('scheduled_jobs'):raise ValueError('prospective collection must have explicit original slots')
    return {(config['application'],j['group_id'],j['original_schedule_slot']):
            (j['condition_id'],j['repeat'],j['arm']) for j in config['scheduled_jobs']}


def pack(original_queue,source_queues,out,cohort):
    original_queue=Path(original_queue).resolve();original=read(original_queue/'manifest.json');expected={}
    for job in original['jobs']:
        if job.get('kind')=='gate':continue
        path,_,_=command_paths(job);config=read(path);slots=expected_slots(config)
        if set(expected)&set(slots):raise ValueError('duplicate original slot ownership')
        if len(slots)!=len(config['scheduled_jobs']):raise ValueError('duplicate slot within source configuration')
        expected.update(slots)
    queues=[original_queue,*map(lambda p:Path(p).resolve(),source_queues)]
    if len(set(queues))!=len(queues):raise ValueError('duplicate source queue')
    sources=[];seen_sources=set();rows=[];history=defaultdict(list);protocols={}
    for queue in queues:
        for job in read(queue/'manifest.json')['jobs']:
            if job.get('kind')=='gate':continue
            config_file,directory,runtime=command_paths(job)
            directory=directory.resolve()
            if directory in seen_sources:raise ValueError('duplicate source directory')
            seen_sources.add(directory)
            if not directory.exists():continue # unlaunched original replaced by a registered resume source
            config=read(directory/'protocol.json')
            if config!=read(config_file):raise ValueError('launched config differs from registered queue')
            summary=read(directory/'summary.json')
            if summary['status'] not in ('complete','stopped'):raise ValueError('source is not terminal')
            recovery=None
            if (summary['campaign_baseline_restore']['status']!='restored'
                    or not summary['campaign_baseline_restore'].get('native_canaries')
                    or not all(summary['campaign_baseline_restore']['native_canaries'].values())):
                receipt=directory/'TERMINAL_RECOVERY.json'
                if not receipt.exists():raise ValueError('source final restore unverified; preserve summary and attach a reviewed recovery before use')
                recovery=validate_terminal_recovery(directory,config,summary,read(receipt))
            receipt=directory/'TERMINAL_RECOVERY.json'
            if receipt.exists() and read(receipt).get('scope_kind')=='post_outcome_cleanup':
                recovery=validate_terminal_recovery(directory,config,summary,read(receipt))
            files={p.name:sha(p) for p in sorted((directory/'source_snapshot').glob('*.py'))}
            if not files:raise ValueError('source snapshot absent')
            for name,digest in files.items():
                package='rca_bench' if name in ('recovery_oracle.py','workload_oracle.py') else 'methods/E2R/intervention'
                if sha(runtime/package/name)!=digest:raise ValueError('runtime and executed snapshot differ')
            identity=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()
            source={'source_directory':str(directory),'runtime_path':str(runtime),'source_snapshot_sha256':identity,
                    'protocol_sha256':sha(directory/'protocol.json'),'source_snapshot_files':files,
                    'summary_status':summary['status'],'queue':str(queue),'job_id':job['id']}
            if recovery is not None:source['terminal_health_recovery']=recovery
            protocol=stratum_id(protocol_descriptor(config,source));protocols[protocol]=protocol_descriptor(config,source)
            reference=EvidenceGraph(0.,[Entity(**e) for e in config['inventory']['entities']],[],[],config['inventory']['goals'])
            catalog={p['id']:Plan.from_dict(p) for p in config['plans']};cases={(c['group_id'],c['condition_id']):c for c in config['cases']}
            scheduled=config['scheduled_jobs'];ledger=[json.loads(line) for line in (directory/'episodes.jsonl').read_text().splitlines() if line.strip()]
            if len(ledger)!=summary['attempted'] or len(ledger)>len(scheduled):raise ValueError('source attempt count differs')
            source_id=cohort+'/source-'+str(len(sources)).zfill(3)
            for index,raw in enumerate(ledger):
                episode=directory/raw['episode_id']/'episode.json'
                if episode.resolve().parent.parent!=directory:raise ValueError('raw episode path escapes its source')
                if read(episode)!=raw:raise ValueError('raw episode and ledger differ')
                assigned=scheduled[index];slot=raw.get('original_schedule_slot')
                key=(raw['application'],raw['group_id'],slot)
                assigned_arm=raw.get('assigned_method') if config.get('mode') in ('laboratory_policy_comparison','goal_policy_comparison') else raw['chosen_plan']
                actual=(raw['condition_id'],raw['repeat'],assigned_arm)
                if key not in expected or actual!=expected[key] or slot!=assigned['original_schedule_slot']:
                    raise ValueError('attempt does not match prospective original slot')
                if assigned_arm!=assigned['arm'] or any(raw[k]!=assigned[a] for k,a in [('group_id','group_id'),('condition_id','condition_id'),('repeat','repeat')]):
                    raise ValueError('attempt differs from its immediate source assignment')
                if raw['source']!='live':raise ValueError('non-native attempt in native data')
                graph,plan=validate_record(raw)
                # A setup failure precedes policy selection; keep its denominator
                # without inventing the action the policy would have chosen.
                registered=catalog.get(raw['chosen_plan'])
                if registered is None and not (config.get('mode') in ('laboratory_policy_comparison','goal_policy_comparison') and graph is None):
                    raise ValueError('recorded candidate is absent from action catalog')
                signature=plan_signature(reference,registered) if registered is not None else None
                if plan is not None and (plan.to_dict()!=registered.to_dict() or plan_signature(graph,plan)!=signature):raise ValueError('observed candidate differs from action catalog')
                if raw.get('operation_policy')!=config['policy']:raise ValueError('operation policy differs from source approval')
                row=copy.deepcopy(raw);row['episode_id']=source_id+'/'+raw['episode_id'];row['artifact_directory']=str(episode.parent)
                row['protocol_stratum']=protocol
                row['source_provenance']={**{k:v for k,v in source.items() if k!='source_snapshot_files'},
                    'source_id':source_id,'original_episode_id':raw['episode_id'],'raw_episode_sha256':sha(episode),
                    'original_schedule_slot':slot,'plan_signature':signature,'action_count':len(registered.actions) if registered is not None else None,
                    'registered_diagnosis_label':cases[(raw['group_id'],raw['condition_id'])].get('diagnosis_label')}
                rows.append(row);history[key].append(row)
            source.update(source_id=source_id,attempts=len(ledger),protocol_stratum=protocol,ledger_sha256=sha(directory/'episodes.jsonl'),summary_sha256=sha(directory/'summary.json'))
            sources.append(source)
    missing=[]
    for key in expected:
        attempts=sorted(history[key],key=lambda r:(r.get('completed_at',0),r['episode_id']))
        covered=[r for r in attempts if r['status'] not in ('failed','started') and (r['restore']['status']=='restored' or r['source_provenance']['original_episode_id'] in (r['source_provenance'].get('terminal_health_recovery') or {}).get('recovered_episode_ids',[]))]
        if not covered:missing.append(key)
        elif len(covered)!=1 or attempts[-1]['episode_id']!=covered[0]['episode_id']:raise ValueError('covered slot was repeated')
    if missing:raise ValueError(f'{len(missing)} original slots remain uncovered; complete/recover them before packaging')
    if len({r['episode_id'] for r in rows})!=len(rows):raise ValueError('nonunique packed attempt IDs')
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    with (out/'episodes.jsonl').open('x') as stream:
        for row in rows:stream.write(json.dumps(row,allow_nan=False)+'\n')
    save(out/'sources.json',sources);save(out/'protocol_strata.json',protocols)
    summary={'status':'complete','cohort':cohort,'purpose':'developmental native collection; not confirmatory test',
        'expected_slots':len(expected),'attempts':len(rows),'sources':len(sources),'groups':len({(r['application'],r['group_id']) for r in rows}),
        'statuses':dict(Counter(r['status'] for r in rows)),'protocol_strata':len(protocols),'no_input':sum('input' not in r for r in rows),
        'effect_denominators':{k:dict(Counter('unknown' if r.get('outcome',{}).get('effects',{}).get(k) is None else str(r['outcome']['effects'][k]).lower() for r in rows)) for k in ('business_recovered','non_target_safe')},
        'data_sha256':sha(out/'episodes.jsonl'),'original_manifest':str(original_queue/'manifest.json'),'original_manifest_sha256':sha(original_queue/'manifest.json'),
        'completed_at':time.time(),'history_preserved':True}
    save(out/'summary.json',summary);print(json.dumps(summary))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--original-queue',required=True,type=Path);p.add_argument('--source-queue',action='append',default=[],type=Path);p.add_argument('--out',required=True,type=Path);p.add_argument('--cohort',required=True);a=p.parse_args();pack(a.original_queue,a.source_queue,a.out,a.cohort)
