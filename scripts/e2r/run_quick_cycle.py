#!/usr/bin/env python3
"""Sequential quick experiment stages; no adaptive outcome-based design changes."""
import argparse,json,resource,sys,time
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.learning_v2 import fit_v2,RelationalPredictor,save,check_partitions
from methods.E2R.intervention.data import group_key,validate_record
from methods.E2R.intervention.schema import Plan
from scripts.e2r.run_exploratory_training import load_records,evaluate
from scripts.e2r.pack_revision_native import pack
from scripts.e2r.analyze_online_comparison import attempt,summarize,paired

def load_fit(plan):
    rows,split=load_records(json.loads(Path(plan['base_training_plan']).read_text()))
    extra=[json.loads(l) for l in (Path(plan['out'])/'pilot_data/episodes.jsonl').read_text().splitlines()]
    for r in extra:validate_record(r)
    split['train']=split['train']+sorted({group_key(r) for r in extra})
    rows+=extra
    if len({r['episode_id'] for r in rows})!=len(rows):raise ValueError('duplicate fitting attempt')
    check_partitions(rows,split)
    return rows,split

def run(plan_path,stage,identity=None):
    plan=json.loads(Path(plan_path).read_text());out=Path(plan['out'])
    if str(ROOT)!=plan['runtime']:raise ValueError('use frozen quick runtime')
    release=json.loads((out/'RELEASE.json').read_text())
    if release.get('status')!='passed':raise ValueError('quick code review release required')
    if stage=='prepare':
        pack(out/'pilot',[],out/'pilot_data','quickfit')
        pilot=[json.loads(l) for l in (out/'pilot_data/episodes.jsonl').read_text().splitlines()];cells=defaultdict(list)
        for row in pilot:cells[(row['application'],row['condition_id'],row['chosen_plan'])].append(row)
        diagnostic=[]
        for key,samples in sorted(cells.items()):
            diagnostic.append({'application':key[0],'condition':key[1],'arm':key[2],'attempts':len(samples),
                'recovered':sum(r.get('outcome',{}).get('effects',{}).get('business_recovered') is True for r in samples),
                'safe':sum(r.get('outcome',{}).get('effects',{}).get('non_target_safe') is True for r in samples),
                'unknown':sum(any(r.get('outcome',{}).get('effects',{}).get(k) is None for k in ('business_recovered','non_target_safe')) for r in samples)})
        save(out/'PILOT_DIAGNOSTIC.json',{'status':'complete','cells':diagnostic,'scope':'2repeats perarm/condition, descriptive only; doesnot establish statistically supported C1 pairs. Negative outcomes do not gate later stages.'})
        rows,split=load_fit(plan);profiles=[]
        for variant in ('graph','local'):
            p=next(m for m in plan['models'] if m['parameters']['variant']==variant and m['parameters']['objective']=='effect_action_rank')
            result=fit_v2(rows,split,out/'profiles'/variant,**{**p['parameters'],'epochs':2},run_metadata={'scope':'engineering train/development only'})
            history=json.loads((out/'profiles'/variant/'seed42_history.json').read_text())
            if result['training']['action_rank_pair_count']<1 or not any(h['action_rank_loss']>0 for h in history):raise ValueError('action-ranking engineering path has no signal')
            profiles.append({'variant':variant,'rank_pairs':result['training']['action_rank_pair_count']})
        peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
        available=next(int(l.split()[1])/1024 for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:'))
        if available<2*peak+4096:raise ValueError('insufficient two-worker training memory headroom')
        save(out/'PREPARED.json',{'status':'passed','profiles':profiles,'pilot_attempts':24,'old_test_evaluated':False,
            'peak_rss_mib':peak,'mem_available_mib':available,'completed_at':time.time()})
    elif stage=='train':
        if json.loads((out/'PREPARED.json').read_text())['status']!='passed':raise ValueError('profile missing')
        entry=next(m for m in plan['models'] if m['id']==identity);rows,split=load_fit(plan);started=time.time()
        result=fit_v2(rows,split,entry['out'],**entry['parameters'],run_metadata={'plan':str(plan_path),'scope':plan['scope']})
        predictor=RelationalPredictor(entry['out'],seeds=[42]);dev=[r for r in rows if group_key(r) in split['development']]
        metrics={}
        for view in ('full','initial'):
            v=evaluate(dev,predictor,view=view,minimum_repeats=5);save(Path(entry['out'])/f'development_{view}.json',v)
            metrics[view]={k:v['effects'][k] for k in ('business_recovered','non_target_safe')}
        save(Path(entry['out'])/'QUICK_MODEL_DONE.json',{'status':'complete','model':identity,'old_test_evaluated':False,
            'validation_metrics':metrics,'rank_pairs':result['training']['action_rank_pair_count'],'statistically_supported_pairs':result['training']['pair_count'],
            'wall_seconds':time.time()-started,'peak_rss_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024})
    elif stage=='check_online':
        registration=json.loads((out/'online/PREREGISTRATION.json').read_text())
        declaration=next(a for a in registration['assignments'] if a['id']==plan['first_online_id'])
        folder=Path(declaration['out']);config=json.loads((folder/'protocol.json').read_text())
        summary=json.loads((folder/'summary.json').read_text());rows=[json.loads(l) for l in (folder/'episodes.jsonl').read_text().splitlines()]
        restore=summary['campaign_baseline_restore']
        if summary['status']!='complete' or len(rows)!=1 or restore['status']!='restored' or not all(restore['native_canaries'].values()):raise ValueError('first online source did not restore')
        row=rows[0];_,chosen=validate_record(row)
        if row['assigned_method']!='fixed' or config['quick_policy_id']!='graph_rank_fixed2':raise ValueError('wrong first policy')
        if chosen is None or chosen.to_dict()!=Plan.from_dict(next(p for p in config['plans'] if p['id']==row['chosen_plan'])).to_dict():raise ValueError('unregistered first action')
        if row['restore']['status']!='restored' or 'decision' not in row:raise ValueError('first decision not observed')
        if json.loads((folder/row['episode_id']/'episode.json').read_text())!=row:raise ValueError('first raw record differs')
        budget=row['total_probe_budget'];active=[e for e in budget['events'] if e['phase']=='adaptive' and e.get('started')]
        if len(active)>2 or any(e['probe_id'] not in config['fixed_adaptive_probe_ids'] for e in active):raise ValueError('fixed2 did not restrict active queries')
        if budget['calls']>16 or budget['cost']>48 or budget['actual_runtime_exceeded']:raise ValueError('first total budget exceeded')
        if row['execution']['status']=='executed' and (not budget['pre_write_authorization_checks'] or not all(x['approved'] and x['elapsed_seconds']<=180 for x in budget['pre_write_authorization_checks'])):raise ValueError('first write budget unverified')
        if row['status']=='observed' and not row['outcome']['verification'].get('during_strategy_non_target'):raise ValueError('first query safety interval missing')
        save(out/'ONLINE_SANITY.json',{'status':'passed','source':str(folder),'included_positions':1,'active_probe_ids':[e['probe_id'] for e in active],
            'scope':'Native new fixed2 path, budgets, raw integrity and restoration only; scientific recovery/safety success not required.'})
    elif stage=='summarize':
        pack(out/'online',[],out/'online_data','quickonline')
        raw=[json.loads(l) for l in (out/'online_data/episodes.jsonl').read_text().splitlines()];records=[]
        for r in raw:
            c=json.loads((Path(r['source_provenance']['source_directory'])/'protocol.json').read_text());a=attempt(r,c);a['strategy']=c['quick_policy_id'];records.append(a)
        labels=['graph_rank_guided','local_rank_guided','graph_effect_guided','graph_rank_fixed2']
        if len(records)!=32 or any(sum(r['strategy']==label for r in records)!=8 for label in labels):raise ValueError('quick online matrix incomplete')
        stats={s:summarize([r for r in records if r['strategy']==s]) for s in labels}
        comparisons=[]
        for other in labels[1:]:
            value=paired(records,labels[0],other);value['scope']='Exploratory matched8blocks across2known groups; no confirmation/noninferiority inference.';comparisons.append(value)
        target=out/'results';target.mkdir(exist_ok=False)
        result={'status':'complete','pilot_attempts':24,'online_attempts':32,'strategies':stats,'paired':comparisons,
            'models':{m['id']:json.loads((Path(m['out'])/'QUICK_MODEL_DONE.json').read_text()) for m in plan['models']},
            'scope':plan['scope'],'completed_at':time.time()}
        save(target/'FINAL.json',result);save(target/'ATTEMPTS.json',records)
        lines=['# 快速完整顺序实验结果','','单种子、小样本、已知配置探索；不等同C1/C2确认。','',
               '| 策略 | 实际执行 | 恢复 | 非目标安全 | 主动调用均值 | 总成本均值 |','|---|---:|---:|---:|---:|---:|']
        for label,v in stats.items():lines.append(f"| {label} | {v['executed_count']}/8 | {v['recovered']['true']}/8 | {v['safe']['true']}/8 | {v['costs']['active_calls']['mean']:.3f} | {v['costs']['total_cost']['mean']:.3f} |")
        (target/'REPORT.md').write_text('\n'.join(lines)+'\n')
    else:raise ValueError('unknown stage')
    print(json.dumps({'stage':stage,'id':identity,'status':'complete'}),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',required=True);p.add_argument('--stage',required=True);p.add_argument('--id');a=p.parse_args();run(a.plan,a.stage,a.id)
