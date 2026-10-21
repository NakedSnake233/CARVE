"""Budget-matched query replay from saved genuine pre-action observations.

This is an observation replay and matched-arm value estimate, not live execution.
It neither consults post-action labels to choose probes nor invents probe answers.
"""
from __future__ import annotations

import copy
import time
from collections import defaultdict
from dataclasses import asdict
from .contracts import Policy
from .data import group_key
from .evaluation import policy_for_record, replay_groups, replay_outcomes
from .protocols import anchor_key, attempt_denominators
from .platform import Probe
from .runtime import ObservationBudget, refine_and_decide
from .schema import EFFECTS, EvidenceGraph, Fact, GraphObservation, Entity, Relation, input_bundle


class RecordedObservations:
    def __init__(self, anchor):
        self.anchor=anchor
        self.records={r['probe']['id']:r for r in anchor.get('available_pre_action_observations',[])}
        self.started=time.monotonic()
        self.query_seconds=0.0
        self.wall_origin=anchor['input']['graph']['at']

    def elapsed(self):
        return time.monotonic()-self.started+self.query_seconds

    def wall(self):
        return self.wall_origin+self.elapsed()

    def observe(self, probe, graph):
        row=self.records[probe.id]
        observation=copy.deepcopy(row['observation'])
        self.query_seconds+=row['seconds']
        if row['observation_type']=='fact':
            return Fact(**observation)
        return GraphObservation([Entity(**v) for v in observation['entities']],
            [Fact(**v) for v in observation['facts']], [Relation(**v) for v in observation['relations']])


def replay_refinement(rows, policy, predictor, *, strategy='decision', budget=ObservationBudget(), seed=42, fixed_order=None,
                      protocol_matching=True):
    grouped,counts=replay_groups(rows,protocol_matching)
    results=[]
    for (group,condition,protocol),samples in sorted(grouped.items()):
        anchors=[r for r in samples if 'input' in r and 'masked_graph' in r]
        decision=None;selected=None;plans=[];anchor=None
        if anchors:
            anchor=min(anchors,key=anchor_key)
            replay=RecordedObservations(anchor)
            bundle=copy.deepcopy(anchor['input'])
            bundle['graph']=copy.deepcopy(anchor['masked_graph'])
            # All saved readings predate this original action boundary. Keep their
            # measured timestamps; virtual replay time only accounts query cost.
            bundle['graph']['at']=replay.wall_origin
            graph,plans=input_bundle(bundle)
            probes=[Probe(**r['probe']) for r in anchor.get('available_pre_action_observations',[])]
            decision=refine_and_decide(graph,plans,policy_for_record(anchor,policy),predictor,probes,replay.observe,
                strategy=strategy,budget=budget,clock=replay.elapsed,wall_clock=replay.wall,seed=seed,fixed_order=fixed_order)
            selected=next((r for r in decision['candidates'] if r['plan_id']==decision['selected_plan']),None)
            decision['saved_query_seconds_charged']=replay.query_seconds
            decision['replay_scope']='saved pre-action query answers; no live probe or intervention'
        outcomes,present,evidence=replay_outcomes(samples,selected,anchor)
        results.append({'group':group,'condition':condition,'plan_id':selected['plan_id'] if selected else None,
            'protocol_stratum':protocol,'condition_weight':1/counts[(group,condition)],
            'score':selected['utility'] if selected else None,
            'has_actions':bool(next((p.actions for p in plans if selected and p.id==selected['plan_id']),())),
            'attempted_episodes':len(samples),'matching_arm_present':present,'outcomes':outcomes,
            'refinement':decision,'strategy':strategy,'seed':seed,'limits':asdict(budget),
            **attempt_denominators(samples), **evidence})
    return results
