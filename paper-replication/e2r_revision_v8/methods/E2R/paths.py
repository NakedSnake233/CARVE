"""Relocatable workspace paths for E2R supplementary experiments."""
from pathlib import Path
import os

PROJECT_ROOT=Path(__file__).resolve().parents[2]


def default_dyrca_root():
    return Path(os.environ.get('DYRCA_ROOT',PROJECT_ROOT.parent/'dyrca')).expanduser().resolve()


def observation_file(row,manifest_path):
    """Resolve new relative snapshots and old peer-directory absolute manifests."""
    manifest_path=Path(manifest_path).resolve()
    saved=Path(row['observation_file'])
    if not saved.is_absolute():
        candidate=manifest_path.parent/saved
    else:
        peer=manifest_path.parent/'observations'/saved.name
        candidate=peer if peer.is_file() else saved
    if not candidate.is_file():
        raise FileNotFoundError(f'Observation unavailable for {row["case_id"]}: {candidate}')
    return candidate.resolve()
