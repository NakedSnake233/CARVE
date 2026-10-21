"""Training-only empirical action contrasts; not statistical C1 pair evidence."""
from collections import defaultdict
import itertools
import json
from .evaluation import observed_plan_signature
from .protocols import protocol_key


def action_rank_pairs(rows,input_ids,minimum_repeats=5,minimum_gap=.1):
    indices={identity:i for i,identity in enumerate(input_ids)}
    cells=defaultdict(lambda:defaultdict(list))
    for row in rows:
        signature=observed_plan_signature(row)
        if signature is None or not json.loads(signature):continue
        cells[(row['application'],row['comparison_group'],row['condition_id'],protocol_key(row))][signature].append(row)
    pairs=[]
    for context,arms in cells.items():
        summaries=[]
        for signature,samples in arms.items():
            known=[r for r in samples if all(r.get('outcome',{}).get('effects',{}).get(k) is not None for k in ('business_recovered','non_target_safe'))]
            members=[indices[r['episode_id']] for r in samples if r['episode_id'] in indices]
            if len(known)<minimum_repeats or not members:continue
            n=len(samples);success=sum(r['outcome']['effects']['business_recovered'] is True for r in samples if 'outcome' in r)
            harm=sum(r['outcome']['effects']['non_target_safe'] is False for r in samples if 'outcome' in r)
            missing_r=sum(r.get('outcome',{}).get('effects',{}).get('business_recovered') is None for r in samples)
            missing_s=sum(r.get('outcome',{}).get('effects',{}).get('non_target_safe') is None for r in samples)
            cost=.01*len(json.loads(signature))
            summaries.append({'members':members,'signature':signature,'attempts':n,'known':len(known),'action_cost':cost,
                              'lower':(success-harm-missing_s)/n-cost,'upper':(success+missing_r-harm)/n-cost})
        for a,b in itertools.combinations(summaries,2):
            winner,loser=(a,b) if a['lower']>b['upper'] else (b,a)
            gap=winner['lower']-loser['upper']
            if gap>minimum_gap:
                pairs.append({'context':context,'winner':winner,'loser':loser,'weight':gap,
                              'scope':'empirical missing-outcome bounds only; not a sampling-confidence contrast'})
    return pairs
