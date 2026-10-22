#!/usr/bin/env python3
"""Read multi-queue collection progress without double-counting source attempts."""
import argparse
import json
import time
from collections import Counter,defaultdict
from pathlib import Path


def collect(index_path):
    index=json.loads(Path(index_path).read_text());sources=set();queue_views=[]
    for directory in index['lineage_queues']:
        am=json.loads((Path(directory)/'AMENDMENT.json').read_text())
        sources.update(Path(x['directory']).resolve() for x in am.get('original_campaigns',[]))
        for entry in am['assignments']:
            sources.add(Path(entry['path']).resolve())
            sources.update(Path(p).resolve() for p in entry.get('attempt_sources',[]))
        for entry in am.get('source_coverage',[]):
            sources.update(Path(p).resolve() for p in entry.get('attempt_sources',[]))
    for declaration in index['active_queues']:
        q=Path(declaration['directory']);am=json.loads((q/'AMENDMENT.json').read_text())
        state_path=q/'queue_state.json'
        if not state_path.exists():
            queue_views.append({'directory':str(q),'status':'not_started','running':[]});continue
        state=json.loads(state_path.read_text());selected=set(declaration['namespaces'])
        entries={a['id']:a for a in am['assignments'] if a['namespace'] in selected}
        statuses=Counter();running=[]
        for name,entry in entries.items():
            row=state['jobs'][name];statuses[row['status']]+=1
            if row['status'] in ('running','launching'):
                running.append({'campaign':name,'namespace':entry['namespace'],'worker_pid':row.get('pid')})
        queue_views.append({'directory':str(q),'status':state['status'],'namespaces':sorted(selected),
            'job_counts':dict(statuses),'running':running,'heartbeat_age_seconds':time.time()-state['updated_at'],
            'dispatch_delay_floor_seconds':declaration['dispatch_delay_floor_seconds']})
    totals=Counter();by_app=defaultdict(Counter);covered=set();attempted=set();partial=[];protocol_counts=Counter()
    for source in sorted(sources):
        if not (source/'protocol.json').exists():continue
        config=json.loads((source/'protocol.json').read_text())
        if config.get('stage')!='formal':raise ValueError('non-formal source in collection index: '+str(source))
        for path in sorted(source.glob('episode-*/episode.json')):
            try:row=json.loads(path.read_text())
            except json.JSONDecodeError:
                partial.append(str(path));continue
            position=int(path.parent.name.removeprefix('episode-'))
            assigned=config.get('scheduled_jobs')
            slot=assigned[position]['original_schedule_slot'] if assigned is not None else position
            if assigned is not None and row.get('original_schedule_slot')!=slot:
                raise ValueError('missing original-slot provenance: '+str(path))
            key=(row['application'],row['group_id'],slot)
            attempted.add(key);totals[row['status']]+=1;by_app[row['application']][row['status']]+=1
            protocol_counts[str(config.get('dispatch_delay_floor_seconds',.25))]+=1
            if row['status'] not in ('failed','started') and row.get('restore',{}).get('status')=='restored':covered.add(key)
    applications=[]
    for app,planned in index['planned_slots_by_application'].items():
        count=sum(k[0]==app for k in covered)
        applications.append({'application':app,'planned_slots':planned,'covered_slots':count,
            'remaining_slots':planned-count,'attempts':sum(by_app[app].values()),'statuses':dict(by_app[app])})
    return {'checked_at':time.time(),'queues':queue_views,'active_jobs_reported':sum(len(q['running']) for q in queue_views),
        'source_directories':len(sources),'total_attempts':sum(totals.values()),'statuses':dict(totals),
        'attempted_unique_slots':len(attempted),'covered_slots':len(covered),'planned_slots':sum(index['planned_slots_by_application'].values()),
        'applications':applications,'attempts_by_dispatch_floor':dict(protocol_counts),'partial_episode_files':partial,
        'note':'Queue heartbeat/job status only; process liveness must be checked in the host PID namespace.'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--index',type=Path,required=True)
    p.add_argument('--out',type=Path);args=p.parse_args();result=collect(args.index)
    if args.out:args.out.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps(result,ensure_ascii=False))
