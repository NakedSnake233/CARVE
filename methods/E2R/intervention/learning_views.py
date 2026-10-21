"""Explicit observed input views; preserve the collected outcome/protocol scope."""
import copy
from .schema import EvidenceGraph, input_bundle


def record_for_view(row, view='full'):
    if view not in ('full', 'initial'):
        raise ValueError('unknown learning input view')
    if view == 'full' or 'input' not in row:
        return row
    if 'masked_graph' not in row:
        raise ValueError('initial view missing; full observations cannot replace it')
    initial = EvidenceGraph.from_dict(row['masked_graph'])
    full, _ = input_bundle(row['input'])
    if initial.at > full.at:
        raise ValueError('initial input view was observed after the full input')
    # Observations added by later probes must never flow backwards into this view.
    result = copy.deepcopy(row)
    result['input']['graph'] = initial.to_dict()
    input_bundle(result['input'])
    result['derived_learning_view'] = {
        'view': 'initial', 'source': 'recorded masked_graph',
        'outcome_scope': 'unchanged full collection protocol; not no-query policy efficacy'}
    return result
