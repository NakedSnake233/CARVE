"""Synthetic formal-M2 contract only; generated outputs are not research results."""
import hashlib,json,subprocess,sys
from pathlib import Path
import pytest

from methods.E2R.graph import operation_catalog,use_upstream
from methods.E2R.llm_pilot import FORMAL_ROUTES,validate_output
from methods.E2R.paths import default_dyrca_root,observation_file

ROOT=Path(__file__).resolve().parents[2]


def test_formal_three_route_scoring_and_analysis(tmp_path):
    checkpoints=ROOT/'experiments/e2r/runs/gpu_full_4090d'
    if not (checkpoints/'model_seed44.pt').is_file():pytest.skip('M1 checkpoints unavailable')
    use_upstream(default_dyrca_root());manifest_path=ROOT/'experiments/e2r/runs/full_data_v2/manifest.json'
    manifest=json.loads(manifest_path.read_text());by={r['case_id']:r for r in manifest['cases']}
    split=json.loads((checkpoints/'split.json').read_text());selected=split['test'][:2]
    observations={c:json.loads(observation_file(by[c],manifest_path).read_text()) for c in selected}
    campaign_dir=tmp_path/'campaign';campaign_dir.mkdir();campaign={'schema':'e2r_m2_formal_v1',
        'model_alias':'synthetic','request_model':'synthetic','api_style':'openai',
        'actual_request_settings':{'api_style':'openai','model':'synthetic'},
        'cases':[{'case_id':c,'app':by[c]['app'],'scenario':by[c]['scenario']} for c in selected],
        'generations':1,'routes':list(FORMAL_ROUTES),'authorized_request_ceiling':6}
    (campaign_dir/'campaign.json').write_text(json.dumps(campaign));events=[]
    (campaign_dir/'frozen_inputs.json').write_text(json.dumps(observations))
    labels={c:{key:by[c][key] for key in ('app','scenario','true_service')} for c in selected}
    (campaign_dir/'evaluator_labels.json').write_text(json.dumps(labels))
    campaign['frozen_model_inputs']='frozen_inputs.json';campaign['separate_evaluator_labels']='evaluator_labels.json'
    (campaign_dir/'campaign.json').write_text(json.dumps(campaign))
    for case in selected:
        obs=observations[case];catalog=operation_catalog(obs)
        for route in FORMAL_ROUTES:
            request_id=f'{case}-g0-{route}';raw={'ranking':obs['candidates'],'abstain':False,'rationale':'synthetic'}
            if route=='proposal':raw['selected_operation_ids']=[x['id'] for x in catalog]
            text=json.dumps(raw);events.append({'event':'request_started','request_id':request_id,'case_id':case,'generation':0,'route':route})
            events.append({'event':'request_finished','request_id':request_id,'case_id':case,'generation':0,'route':route,
                           'transport_success':True,'response_text':text,'usage':{'total_tokens':1},'provider_cost':'0.001','request_seconds':.01,
                           'validation':validate_output(text,route,obs,catalog)})
    (campaign_dir/'requests.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in events))
    (campaign_dir/'status.json').write_text(json.dumps({'status':'complete','actual_http_attempts_started':6}))
    hash_file=tmp_path/'checkpoint_hashes.json'
    hash_file.write_text(json.dumps({f'model_seed{seed}.pt':hashlib.sha256(
        (checkpoints/f'model_seed{seed}.pt').read_bytes()).hexdigest() for seed in (42,43,44)}))
    scores=tmp_path/'scores';subprocess.run([sys.executable,str(ROOT/'scripts/e2r/score_m2_formal.py'),
        '--campaign-dir',str(campaign_dir),'--manifest',str(manifest_path),'--checkpoints',str(checkpoints),
        '--checkpoint-hashes',str(hash_file),
        '--out-dir',str(scores),'--device','cpu'],cwd=ROOT,check=True,capture_output=True)
    result=json.loads((scores/'results.json').read_text());assert result['prediction_rows']==36
    analysis=tmp_path/'analysis';subprocess.run([sys.executable,str(ROOT/'scripts/e2r/analyze_m2_formal.py'),
        '--score-dir',str(scores),'--campaign-dir',str(campaign_dir),'--out-dir',str(analysis)],cwd=ROOT,check=True,capture_output=True)
    output=json.loads((analysis/'analysis.json').read_text())
    assert output['requests']['attempts']==6 and output['requests']['provider_cost_total']==.006
    assert output['scope']['incidents']==2
