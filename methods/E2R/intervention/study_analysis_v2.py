"""V2 contrast metrics matched to same-diagnosis repeated business/safety evidence."""
import statistics
from collections import Counter
from .effect_pairs import repeated_effect_pairs,EFFECT_PAIR_KEYS
from .study_analysis import prediction_metrics
from .learning_views import record_for_view
from .data import validate_record


def prediction_metrics_v2(rows,predictor,minimum_repeats=5):
    view=getattr(predictor,'metadata',{}).get('input_view','full')
    if view=='initial':
        for row in rows:validate_record(row,allow_synthetic=row['source']=='synthetic')
    report=prediction_metrics([record_for_view(r,view) for r in rows],predictor,minimum_repeats)
    report['input_view']=view
    report['outcome_scope']='Original collection protocol and all actually performed probes; no-query policy efficacy is not inferred.'
    predictions={r['attempt_id']:r for r in report['predictions']}
    ids=[r['episode_id'] for r in rows if 'input' in r]
    pairs=repeated_effect_pairs(rows,ids,minimum_repeats)
    scored=[]
    for pair in pairs:
        means=[]
        for members in (pair['left_members'],pair['right_members']):
            values={effect:[predictions[ids[i]]['probabilities'].get(effect) for i in members] for effect in EFFECT_PAIR_KEYS}
            means.append({effect:statistics.mean(v for v in value if v is not None) if any(v is not None for v in value) else None for effect,value in values.items()})
        if any(value is None for side in means for value in side.values()):continue
        differences=[abs(means[0][k]-means[1][k]) for k in EFFECT_PAIR_KEYS]
        predicted=max(differences)>.25
        empirical_differences=[abs(pair['left_counts'][k]['success']/pair['left_counts'][k]['known']-
            pair['right_counts'][k]['success']/pair['right_counts'][k]['known']) for k in EFFECT_PAIR_KEYS]
        empirical_large=max(empirical_differences)>.25
        scored.append({**pair,'left_attempt_ids':[ids[i] for i in pair['left_members']],
            'right_attempt_ids':[ids[i] for i in pair['right_members']],
            'predicted_distance':statistics.mean(differences),'empirical_max_effect_difference':max(empirical_differences),
            'predicted_large_effect_change':predicted,'empirical_large_effect_change':empirical_large,
            'fixed_margin_correct':predicted==empirical_large})
    kinds={kind:[r for r in scored if r['kind']==kind] for kind in ('different','equivalent')}
    report['distinguishability']={'schema':'same_diagnosis_effect_pairs_v2','effects':list(EFFECT_PAIR_KEYS),
        'kind_counts':dict(Counter(p['kind'] for p in pairs)),'scored_pairs':len(scored),'unscored_pairs':len(pairs)-len(scored),
        'primary_metric':'continuous_repeated_condition_distance_mae',
        'accuracy':None,'kind_balanced_accuracy':None,
        'fixed_margin_point_accuracy':statistics.mean(p['fixed_margin_correct'] for p in scored) if scored else None,
        'classification_semantics':'secondary empirical-mean delta>.25 on both truth and prediction; not statistical-separation accuracy or a confidence claim',
        'always_different_accuracy':None,'always_equivalent_accuracy':None,
        'always_large_effect_change_accuracy':statistics.mean(p['empirical_large_effect_change'] for p in scored) if scored else None,
        'always_small_effect_change_accuracy':statistics.mean(not p['empirical_large_effect_change'] for p in scored) if scored else None,
        'distance_mae':statistics.mean(abs(p['predicted_distance']-p['distance']) for p in scored) if scored else None,
        'classification_threshold':.25,'pairs':scored,
        'scope':'same fine diagnosis, exact protocol/action, business/safety-only repeated outcomes, unknown-inclusive support intervals, mean prediction over input-bearing repeats; predictive classification threshold is fixed'}
    report['representation_scope']='v2 entity states with bound latest facts; entity counts are not comparable to v1 factor-node counts'
    return report
