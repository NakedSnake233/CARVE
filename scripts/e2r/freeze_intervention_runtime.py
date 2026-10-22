#!/usr/bin/env python3
"""Copy the experiment's Python runtime so long jobs do not read changing source files."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import time

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();root=Path(__file__).resolve().parents[2];a.out.mkdir(parents=True,exist_ok=False)
    for name in ('methods','rca_bench'):
        shutil.copytree(root/name,a.out/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.pt','*.pth'))
    (a.out/'scripts').mkdir()
    shutil.copytree(root/'scripts/e2r',a.out/'scripts/e2r',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ('pyproject.toml',):shutil.copy2(root/name,a.out/name)
    (a.out/'RUNTIME.json').write_text(json.dumps({'schema':'intervention_frozen_runtime_v1','created_at':time.time(),
        'source_workspace':str(root),'interpreter':sys.executable,'scope':'Python implementation snapshot; data, credentials and environment remain at their separately declared paths'},indent=2)+'\n')
    print(a.out.resolve())
