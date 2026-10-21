#!/usr/bin/env python3
"""Create a verified pilot completion receipt without overwriting any original result."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.pilot_completion import complete_pilot
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--original',type=Path,required=True)
    p.add_argument('--amendments',type=Path,nargs='+',required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();print(json.dumps(complete_pilot(a.original,a.amendments,a.out)))
