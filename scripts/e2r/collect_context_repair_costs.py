#!/usr/bin/env python3
"""Watcher-compatible refresh of closed-attempt context repair costs."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.e2r.summarize_context_repair import cost_report

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    print(json.dumps(cost_report(args.root)))
