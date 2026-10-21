"""Repeated same-diagnosis business/safety evidence, with unknown-inclusive intervals."""
from collections import defaultdict
import itertools
import random
from .data import wilson
from .evaluation import observed_plan_signature
from .protocols import protocol_key

EFFECT_PAIR_KEYS=('business_recovered','non_target_safe')


def repeated_effect_pairs(rows,input_attempt_ids,minimum_repeats=5,equivalence_margin=.25,max_pairs=1024,seed=42):
    if minimum_repeats<2:raise ValueError('repeated outcome evidence requires multiple observations')
    indices={identity:i for i,identity in enumerate(input_attempt_ids)}
    if len(indices)!=len(input_attempt_ids):raise ValueError('duplicate input attempt identity')
    grouped=defaultdict(list)
    for row in rows:
        signature=observed_plan_signature(row)
        if signature is None:continue
        key=(row['application'],row['comparison_group'],row['condition_id'],signature,protocol_key(row))
        grouped[key].append(row)
    summaries=[]
    for key,samples in grouped.items():
        diagnoses={r['diagnosis_label'] for r in samples if r.get('diagnosis_label') is not None}
        if len(diagnoses)!=1:continue
        # Setup failures may precede the collector's diagnosis field. Preserve their
        # unknown outcomes under the unique sibling assignment for this exact cell.
        key=(*key,next(iter(diagnoses)))
        members=[indices[r['episode_id']] for r in samples if r['episode_id'] in indices]
        means={};intervals={};counts={}
        for effect in EFFECT_PAIR_KEYS:
            values=[r.get('outcome',{}).get('effects',{}).get(effect) for r in samples]
            known=[float(v) for v in values if v is not None];success=sum(known);unknown=len(samples)-len(known)
            counts[effect]={'attempts':len(samples),'known':len(known),'unknown':unknown,'success':success}
            if len(known)>=minimum_repeats:
                means[effect]=success/len(known)
                intervals[effect]=(wilson(success,len(samples))[0],wilson(success+unknown,len(samples))[1])
        if members and len(means)==len(EFFECT_PAIR_KEYS):summaries.append((key,members,means,intervals,counts))
    pairs=[]
    for left,right in itertools.combinations(summaries,2):
        ka,ia,ma,ca,na=left;kb,ib,mb,cb,nb=right
        if ka[3:]!=kb[3:]:continue # exact action, protocol and same evaluator-only fine diagnosis
        different=any(ca[k][1]<cb[k][0] or cb[k][1]<ca[k][0] for k in EFFECT_PAIR_KEYS)
        equivalent=all(max(abs(ca[k][0]-cb[k][1]),abs(ca[k][1]-cb[k][0]))<=equivalence_margin for k in EFFECT_PAIR_KEYS)
        if (different and ka[:2]==kb[:2]) or equivalent:
            pairs.append({'left_members':ia,'right_members':ib,'kind':'different' if different else 'equivalent',
                'distance':sum(abs(ma[k]-mb[k]) for k in EFFECT_PAIR_KEYS)/len(EFFECT_PAIR_KEYS),
                'left_counts':na,'right_counts':nb,'left_intervals':ca,'right_intervals':cb,
                'same_fine_diagnosis':True,'diagnosis_label':ka[-1],'protocol_stratum':ka[4],
                'diagnosis_source':'unique registered sibling assignment within condition/protocol/action; unresolved or mixed cells excluded from pair eligibility',
                'effects':list(EFFECT_PAIR_KEYS),'target_semantics':'known-outcome mean distance only after unknown-inclusive interval evidence; no inferred outcome labels'})
    random.Random(seed).shuffle(pairs)
    return pairs[:max_pairs]
