from pathlib import Path
import json,hashlib
ROOT=Path('${PROJECT_ROOT}');TRACE=Path(__file__).resolve().parent;BASE=ROOT/'experiments/e2r/runs';old=json.loads((BASE/'m2_expenditure_v2/expenditure.json').read_text());new=json.loads((BASE/'m2_expenditure_v3/expenditure.json').read_text())
assert len(old['runs'])==len(new['runs'])==5
for before,after in zip(old['runs'],new['runs']):
    expected=dict(before)
    if not expected['usage_reported_attempts']:expected['cache_inclusive_token_volume']=None
    assert expected==after
expected=dict(old['totals']);expected['cache_inclusive_reported_volume']=expected.pop('cache_inclusive_token_volume');assert new['totals']==expected
assert sum(r['attempts'] for r in new['runs'])==705
assert sum(r['usage_reported_attempts'] for r in new['runs'])==702
assert sum(r['cache_inclusive_token_volume'] for r in new['runs'] if r['cache_inclusive_token_volume'] is not None)==10351081
assert new['totals']['provider_cost_total'] is None
assert 'unknown usage, not zero' in ' '.join(new['limits'])
hashes={}
for p in [BASE/'m2_expenditure_v3/expenditure.json',BASE/'m2_expenditure_v3/REPORT.md',ROOT/'scripts/e2r/summarize_m2_expenditure.py']:
    hashes[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
(TRACE/'expenditure_v3_hashes.json').write_text(json.dumps(hashes,indent=2)+'\n')
(TRACE/'expenditure_v3_verification.json').write_text(json.dumps({'status':'PASS','source':'accounting_v2_verification.json: independently verified all five raw campaign journals','exact_change':'Unreported usage changed from 0 to null and aggregate renamed reported volume; all other counts/arithmetic unchanged.','attempts':705,'reported_usage_attempts':702,'unknown_usage_attempts':3,'reported_cache_inclusive_volume':10351081,'provider_cost':None},indent=2)+'\n')
print('PASS: v3 preserves all independently verified arithmetic and correctly marks missing usage unknown.')
