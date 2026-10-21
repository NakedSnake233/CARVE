#!/usr/bin/env python3
"""Watcher-compatible cost-only refresh for fault coverage."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from scripts.e2r.summarize_fault_coverage import cost_report
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);a=p.parse_args();print(json.dumps(cost_report(a.root)))
