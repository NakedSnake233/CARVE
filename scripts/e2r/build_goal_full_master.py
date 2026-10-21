#!/usr/bin/env python3
"""Wire frozen controls -> assigned holdout -> policy holdout, gated by pilot review."""
import argparse,copy,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]

def build(root,runtime):
    root,runtime=Path(root).resolve(),Path(runtime).resolve();out=root/'full_master';out.mkdir(exist_ok=False)
    py=str(ROOT/'.venv-e2r/bin/python');script=lambda name:str(runtime/'scripts/e2r'/name)
    train=root/'neural_controls';data=root/'full_assigned/data/episodes.jsonl';evaluation=root/'full_evaluation'
    jobs=[{'id':'fit_controls','kind':'gate','command':[py,'-u',script('train_goal_neural_controls.py'),'--data',str(ROOT/'enhancement/data-repair-stage/recovery_fix_validation_20261002_v1/data/episodes.jsonl'),'--out',str(train)],'requires':[str(root/'FULL_RELEASE.json'),str(runtime/'RUNTIME.json')],'outputs':[{'path':str(train/'RESULTS.json'),'json_equals':{'status':'complete'}}]}]
    for queue,prefix,firstdep in [(root/'full_assigned','assigned:','fit_controls'),(root/'full_online_v2','policy:','score_controls')]:
        manifest=json.loads((queue/'manifest.json').read_text())
        for original in manifest['jobs']:
            j=copy.deepcopy(original);j['id']=prefix+j['id'];j['depends_on']=[prefix+d for d in j.get('depends_on',[])] or [firstdep];j.setdefault('requires',[]).append(str(root/'FULL_RELEASE.json'));jobs.append(j)
            if j.get('kind')=='gate':j['command'][2]=script(Path(j['command'][2]).name)
        if prefix=='assigned:':
            jobs.append({'id':'score_controls','kind':'gate','depends_on':['assigned:assigned_results'],'command':[py,'-u',script('evaluate_goal_full.py'),'--data',str(data),'--model',str(root/'transition_model.json'),'--neural',str(train),'--out',str(evaluation)],'outputs':[{'path':str(evaluation/'RESULTS.json'),'json_equals':{'status':'complete'}}]})
    jobs.append({'id':'full_report','kind':'gate','depends_on':['policy:goal_results'],'command':[py,'-u',script('summarize_goal_full.py'),'--root',str(root)],'outputs':[{'path':str(root/'FULL_RESULTS.json'),'json_equals':{'status':'complete'}}]})
    (out/'manifest.json').write_text(json.dumps({'cwd':str(ROOT),'max_parallel':4,'jobs':jobs},indent=2)+'\n')
    return out
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--runtime',required=True);a=p.parse_args();print(build(a.root,a.runtime))
