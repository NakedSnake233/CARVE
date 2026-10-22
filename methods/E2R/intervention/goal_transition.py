"""Goal-conditioned empirical transitions; no scenario IDs or hidden outcomes at inference."""
import json,statistics,time
from collections import defaultdict
from pathlib import Path
from .schema import Plan
from .contracts import Policy,action_option
from .data import validate_record


def goal_state(graph,plan,policy):
    goals=[e for e in graph.entities if e.id in graph.goals and e.role=='goal']
    if not goals:return {'status':'unknown','reason':'no_explicit_state_goal'}
    if len(plan.actions)!=1:return {'status':'unknown','reason':'no_single_intervention'}
    action=plan.actions[0];relations=graph.active_relations();stores={e.id:e for e in graph.entities if e.role=='shared_dependency'}
    owners={r.target for r in relations if r.source in {g.id for g in goals} and r.kind=='depends_on' and r.target in stores}
    if len(owners)!=1:return {'status':'unknown','reason':'nonunique_state_owner'}
    if action.operation=='restore_selector':
        receiver=action.destination
        if graph.read(receiver,'dependencies_known') is not True:return {'status':'unknown','reason':'receiver_dependencies_unknown'}
        bindings={r.target for r in relations if r.source==receiver and r.kind=='competes_for' and r.target in stores}
        source='observed_receiver_dependency_graph'
    elif action.operation=='rollback_version':
        option=action_option(action,policy) or {};template=option.get('template',{})
        addresses={v.get('value','').split('//',1)[-1].split('/',1)[0].split(':',1)[0].split('.',1)[0] for c in template.get('spec',{}).get('containers',[]) for v in c.get('env',[]) if v['name'] in ('REDIS_ADDR','STATION_MYSQL_HOST','SPRING_DATASOURCE_URL')}
        bindings={k for k,e in stores.items() if e.name in addresses};source='registered_rollback_template_binding'
    else:return {'status':'unknown','reason':'unsupported_goal_projection'}
    if len(bindings)!=1:return {'status':'unknown','reason':'nonunique_receiver_storage'}
    return {'status':'pass' if bindings==owners else 'fail','owner_entities':sorted(owners),'receiver_storage_entities':sorted(bindings),'source':source,
            'scope':'single-owner unreplicated immutable acknowledged state protocol; not general replicated-store sufficiency'}


class GoalTransitionModel:
    def __init__(self,metadata):
        if metadata.get('schema')!='goal_transition_model_v3' or metadata.get('source')!='live':raise ValueError('expected registered native transition model')
        self.metadata=metadata
    @classmethod
    def load(cls,path):return cls(json.loads(Path(path).read_text()))
    def predict(self,graph,plan,policy,*,blind=False):
        operation=plan.actions[0].operation if plan.actions else 'no_action'
        witness=goal_state(graph,plan,policy);state=witness['status']
        key=operation+'|'+('pooled' if blind or state=='unknown' else state)
        cell=self.metadata['cells'].get(key,self.metadata['cells'].get(operation+'|pooled'))
        if cell is None:return {'utility':-1.,'success':None,'safety':None,'goal':witness,'support':0}
        success=(cell['business_success']+1)/(cell['business_known']+2)
        safety_cell=self.metadata['cells'][operation+'|pooled']
        safety=(safety_cell['safety_success']+1)/(safety_cell['safety_known']+2)
        seconds=cell['execution_seconds_median']
        return {'success':success,'safety':safety,'execution_seconds':seconds,'goal':witness,'support':cell['attempts'],
                'utility':success-(1-safety)-.002*seconds-.01*len(plan.actions),
                'scope':'smoothed development point estimates, not admission confidence bounds'}


def fit_goal_model(rows,out):
    buckets=defaultdict(list)
    for row in rows:
        graph,plan=validate_record(row)
        if graph is None:continue
        policy=Policy.from_dict(row['operation_policy']);operation=plan.actions[0].operation if plan.actions else 'no_action'
        status=goal_state(graph,plan,policy)['status']
        buckets[operation+'|'+status].append(row);buckets[operation+'|pooled'].append(row)
    cells={}
    for key,rs in buckets.items():
        effects=[r.get('outcome',{}).get('effects',{}) for r in rs]
        timing=[sum(value for name,value in r.get('execution',{}).get('timings_seconds',{}).items() if name.startswith(('write_','controller_'))) for r in rs if r.get('execution',{}).get('status') in ('executed','no_action')]
        cells[key]={'attempts':len(rs),'business_known':sum(e.get('business_recovered') is not None for e in effects),'business_success':sum(e.get('business_recovered') is True for e in effects),
            'safety_known':sum(e.get('non_target_safe') is not None for e in effects),'safety_success':sum(e.get('non_target_safe') is True for e in effects),
            'execution_seconds_median':statistics.median(timing) if timing else 0.}
    result={'schema':'goal_transition_model_v3','source':'live','created_at':time.time(),'cells':cells,'training_attempt_ids':[r['episode_id'] for r in rows],
        'training_groups':sorted({r['application']+'/'+r['group_id'] for r in rows}),
        'calibration_status':'development_only_not_qualified','factorization':'Business recovery conditioned on action+goal; safety conditioned on action (no observed hazard contrast in fitting cohort); transition latency excludes precondition reads which are accounted separately','description':'Learned conditional native outcome counts and measured transition cost; graph ownership witness is explicit, not learned or inferred from case labels.'}
    Path(out).write_text(json.dumps(result,indent=2)+'\n');return result
