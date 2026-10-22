"""Synthetic analysis contracts only; these fixtures are not experiment results."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_paired_analysis_recomputes_known_rankings_and_rejects_bad_rank(tmp_path):
    run = tmp_path / 'synthetic-contract'; run.mkdir()
    cases = [{'case_id': 'a', 'app': 'app1', 'true_service': 'truth'},
             {'case_id': 'b', 'app': 'app2', 'true_service': 'truth'}]
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'cases': cases + [
        {'case_id': 'train-only', 'app': 'app1', 'true_service': 'truth'},
        {'case_id': 'val-only', 'app': 'app1', 'true_service': 'truth'}]}))
    (run / 'split.json').write_text(json.dumps({'train': ['train-only'], 'val': ['val-only'], 'test': ['a', 'b']}))
    records = []; summaries = {}
    for seed in [42, 43, 44]:
        # Presence fixture only; the analysis script never loads model weights.
        (run / f'model_seed{seed}.pt').write_bytes(b'synthetic-file-presence-only')
        for variant in ['reference', 'runtime', 'patch']:
            for case in cases:
                rank = 1 if case['case_id'] == 'a' or variant == 'patch' else 2
                records.append({**case, 'seed': seed, 'variant': variant, 'rank': rank,
                                'ranking': ['truth', 'other'] if rank == 1 else ['other', 'truth']})
            summaries[f'{seed}:{variant}'] = {'AC@1': 1.0 if variant == 'patch' else .5,
                                              'AC@3': 1.0, 'AC@5': 1.0, 'MRR': 1.0 if variant == 'patch' else .75}
    (run / 'results.json').write_text(json.dumps({'status': 'completed_offline_structural_ablation',
        'seeds': [42, 43, 44], 'summary': summaries, 'runtime': {'fixture': True}, 'training': []}))
    raw = run / 'predictions.jsonl'; raw.write_text(''.join(json.dumps(r) + '\n' for r in records))
    command = [sys.executable, str(ROOT / 'scripts/e2r/analyze_m1.py'), '--run-dir', str(run), '--manifest', str(manifest)]
    subprocess.run([*command, '--out-dir', str(tmp_path / 'analysis')], check=True, capture_output=True)
    output = json.loads((tmp_path / 'analysis/analysis.json').read_text())
    assert output['metrics']['all']['patch']['mean']['AC@1'] == 1
    assert output['metrics']['all']['reference']['mean']['AC@1'] == .5
    delta = output['paired_contrasts']['all']['patch-minus-reference']['AC@1']
    assert delta['absolute_delta'] == .5 and delta['n_incidents'] == 2
    assert delta['incident_bootstrap_95ci'] == [0.0, 1.0]
    records[0]['rank'] = 2  # Intentionally inconsistent with saved ranking.
    raw.write_text(''.join(json.dumps(r) + '\n' for r in records))
    failure = subprocess.run([*command, '--out-dir', str(tmp_path / 'invalid')], capture_output=True)
    assert failure.returncode != 0 and not (tmp_path / 'invalid').exists()
