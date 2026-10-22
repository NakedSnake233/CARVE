"""Synthetic-response integration contract; outputs are never research results."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from methods.E2R.graph import operation_catalog, use_upstream
from methods.E2R.llm_pilot import choose_cases, validate_output
from methods.E2R.paths import default_dyrca_root, observation_file

ROOT = Path(__file__).resolve().parents[2]


def test_synthetic_responses_cover_scoring_and_analysis_contract(tmp_path):
    checkpoints = ROOT / 'experiments/e2r/runs/gpu_full_4090d'
    if not (checkpoints / 'model_seed44.pt').is_file():
        pytest.skip('completed M1 checkpoints unavailable')
    use_upstream(default_dyrca_root())
    manifest_path = ROOT / 'experiments/e2r/runs/full_data_v2/manifest.json'
    manifest = json.loads(manifest_path.read_text()); by_id = {r['case_id']: r for r in manifest['cases']}
    split = json.loads((checkpoints / 'split.json').read_text())
    observations = {case: json.loads(observation_file(by_id[case], manifest_path).read_text()) for case in split['test']}
    selected = choose_cases([by_id[c] for c in split['test']], observations, 8, 20260925)
    campaign_dir = tmp_path / 'fake_campaign'; campaign_dir.mkdir()
    campaign = {'schema': 'synthetic_test_only', 'model_alias': 'fake', 'request_model': 'fake',
                'cases': [{'case_id': r['case_id'], 'app': r['app'], 'scenario': r['scenario']} for r in selected],
                'generations': 2, 'hard_request_budget': 32}
    (campaign_dir / 'campaign.json').write_text(json.dumps(campaign))
    events = []
    for row in selected:
        obs = observations[row['case_id']]; catalog = operation_catalog(obs)
        for generation in range(2):
            for route in ('proposal', 'direct'):
                request_id = f'{row["case_id"]}-g{generation}-{route}'
                raw = {'ranking': obs['candidates'], 'abstain': False, 'rationale': 'synthetic contract'}
                if route == 'proposal': raw['selected_operation_ids'] = [entry['id'] for entry in catalog]
                text = json.dumps(raw)
                events.append({'event': 'request_started', 'request_id': request_id, 'case_id': row['case_id'],
                               'generation': generation, 'route': route})
                events.append({'event': 'request_finished', 'request_id': request_id, 'case_id': row['case_id'],
                               'generation': generation, 'route': route, 'transport_success': True,
                               'response_text': text, 'usage': None, 'request_seconds': .01,
                               'validation': validate_output(text, route, obs, catalog)})
    (campaign_dir / 'requests.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in events))
    (campaign_dir / 'status.json').write_text(json.dumps({'status': 'complete',
        'actual_http_attempts_started': 32}))
    score_dir = tmp_path / 'fake_scores'
    subprocess.run([sys.executable, str(ROOT / 'scripts/e2r/score_m2.py'),
        '--campaign-dir', str(campaign_dir), '--manifest', str(manifest_path),
        '--checkpoints', str(checkpoints), '--out-dir', str(score_dir), '--device', 'cpu'],
        cwd=ROOT, check=True, capture_output=True)
    score = json.loads((score_dir / 'results.json').read_text())
    assert score['prediction_rows'] == 200 and score['row_status'] == {'ranked': 200}
    analysis_dir = tmp_path / 'fake_analysis'
    subprocess.run([sys.executable, str(ROOT / 'scripts/e2r/analyze_m2.py'),
        '--score-dir', str(score_dir), '--campaign-dir', str(campaign_dir),
        '--out-dir', str(analysis_dir)], cwd=ROOT, check=True, capture_output=True)
    analysis = json.loads((analysis_dir / 'analysis.json').read_text())
    assert analysis['status'] == 'completed_m2_pilot_analysis'
    assert analysis['requests']['actual_attempts'] == 32
    assert analysis['scope']['incidents'] == 8
