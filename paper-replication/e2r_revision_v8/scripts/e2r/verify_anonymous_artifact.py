#!/usr/bin/env python3
"""Read-only verifier for the publishable, path-scrubbed E2R artifact."""
from __future__ import annotations
import argparse,hashlib,json,re
from pathlib import Path


REQUIRED=[
    'refine-logs/EXPERIMENT_RESULTS.md','refine-logs/EXPERIMENT_TRACKER.md',
    'experiments/e2r/runs/m2_qwen38_combined_analysis_v2/analysis.json',
    'experiments/e2r/runs/m2_qwen38_formal_analysis_v2/analysis.json',
    'experiments/e2r/runs/b2_b3_audit_v1/EXPERIMENT_AUDIT.json',
    'experiments/e2r/runs/m4_sanity_audit_v1/EXPERIMENT_AUDIT.json',
    'experiments/e2r/runs/recovery_matched_frontend_cpu_v1/audit_v1/EXPERIMENT_AUDIT.json',
    '.aris/traces/experiment-audit/20260926_b2_b3/run.meta.json',
    '.aris/traces/experiment-audit/20260927_m4_sanity/run.meta.json',
    '.aris/traces/experiment-audit/20260927_matched_interrupted/run.meta.json',
    '.aris/traces/experiment-audit/20260927_m4_sanity/historical_source/workload_oracle.py',
    'experiments/e2r/splits/generalization_v1/lofo_T1.json',
    'scripts/remote/generalization_worker.py','ANONYMIZATION_MAP.json','VALIDATION.json','ASSURANCE.json',
]


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--artifact-root',default='.')
    parser.add_argument('--out');args=parser.parse_args();root=Path(args.artifact_root).resolve()
    manifest=json.loads((root/'MANIFEST.json').read_text());validation=json.loads((root/'VALIDATION.json').read_text())
    errors=[];rows=manifest.get('files') or []
    for row in rows:
        path=root/row['path']
        if not path.is_file() or path.stat().st_size!=row['bytes'] or sha(path)!=row['sha256']:
            errors.append('manifest:'+row['path'])
    listed={row['path'] for row in rows};actual={str(path.relative_to(root)) for path in root.rglob('*')
        if path.is_file() and path.name!='MANIFEST.json'}
    if listed!=actual:errors.append('manifest-scope')
    for relative in REQUIRED:
        if not (root/relative).is_file():errors.append('required:'+relative)
    mapping=json.loads((root/'ANONYMIZATION_MAP.json').read_text())
    for row in mapping.get('transformed_files',[]):
        path=root/row['path']
        if not path.is_file() or sha(path)!=row['staged_sha256']:
            errors.append('anonymization-map:'+row['path'])
        if not re.fullmatch(r'[0-9a-f]{64}',row.get('source_sha256','')):
            errors.append('source-digest:'+row['path'])
    full=json.loads((root/'experiments/e2r/runs/full_data_v2/manifest.json').read_text())
    seen=set()
    for row in full.get('cases',[]):
        path=root/'experiments/e2r/runs/full_data_v2'/row['observation_file']
        if not path.is_file() or json.loads(path.read_text()).get('case_id')!=row['case_id']:
            errors.append('observation:'+row['case_id'])
        seen.add(row['observation_file'])
    if len(seen)!=798:errors.append('observation-count')
    structural_patterns=[
        re.compile(rb'/home/[A-Za-z0-9._-]+(?:/|\b)'),
        re.compile(rb'[A-Za-z0-9._-]+@(?:\d{1,3}\.){3}\d{1,3}'),
        re.compile(rb'oc_sk_[A-Za-z0-9][A-Za-z0-9_-]{8,}'),
        re.compile(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
        re.compile(rb'\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\b'),
    ]
    for path in root.rglob('*'):
        if not path.is_file():continue
        data=path.read_bytes()
        if any(pattern.search(data) for pattern in structural_patterns):
            errors.append('forbidden:'+str(path.relative_to(root)))
    result={'status':'PASS' if not errors else 'FAIL','manifest_entries':len(rows),
            'observation_cases':len(seen),'required_files':len(REQUIRED),
            'validation_status':validation.get('status'),'errors':errors,
            'note':'Staged hashes are checked through ANONYMIZATION_MAP; source digests are recorded provenance only because private bytes are not bundled.'}
    text=json.dumps(result,indent=2)+'\n'
    if args.out:
        output=Path(args.out).resolve()
        if root==output or root in output.parents:raise ValueError('verifier output must be outside sealed artifact')
        output.write_text(text)
    print(text,end='')
    if errors:raise SystemExit(2)


if __name__=='__main__':main()
