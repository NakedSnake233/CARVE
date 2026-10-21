"""Load the declared effect-model generation without changing its admission rules."""
import json
from pathlib import Path


def load_effect_predictor(directory, *, seeds=None, admission_path=None):
    metadata=json.loads((Path(directory)/'training.json').read_text())
    if metadata.get('schema')=='e2r_intervention_training_v2':
        from .learning_v2 import RelationalPredictor
        return RelationalPredictor(directory,seeds=seeds,admission_path=admission_path)
    if metadata.get('schema')=='e2r_intervention_training_v1':
        if admission_path is not None:
            raise ValueError('v1 model cannot consume a v2 admission ledger')
        from .learning import EffectPredictor
        return EffectPredictor(directory,seeds=seeds)
    raise ValueError('unrecognized effect-model generation')
