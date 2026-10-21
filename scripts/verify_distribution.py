#!/usr/bin/env python3
"""Verify distribution files against MANIFEST; no network, cluster, or model loading."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    manifest=json.loads((ROOT/'MANIFEST.json').read_text());errors=[]
    for row in manifest['files']:
        p=ROOT/row['path']
        if p.is_symlink() or not p.is_file() or p.stat().st_size!=row['bytes'] or hashlib.sha256(p.read_bytes()).hexdigest()!=row['sha256']:
            errors.append(row['path'])
    print(json.dumps({'status':'FAIL' if errors else 'PASS','checked_files':len(manifest['files']),'errors':errors},indent=2))
    return bool(errors)
if __name__=='__main__':raise SystemExit(main())
