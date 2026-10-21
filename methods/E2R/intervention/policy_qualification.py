"""Fixed-selector qualification for one bounded native validation campaign.

This module never searches thresholds or issues a general live-policy release.
"""
from collections import defaultdict
import copy
from dataclasses import asdict
import json
from pathlib import Path
import statistics
import time
from urllib.parse import urlsplit
from .data import group_key,validate_record
from .evaluation import replay_conditions,policy_for_record
from .runtime import rank_candidates
from .schema import EvidenceGraph,input_bundle,number
from .platform import LiveProvider,Probe
from .probe_budget import ProbeAccountant
from .runtime import ObservationBudget
from .bounded_decision import budgeted_decision


def acquisition_scope(config, runtime):
    probes=config['inventory']['probes'];requested=config.get('initial_probes',[])
    order=config.get('initial_probe_order',[p['id'] for p in probes if p['id'] in requested])
    if len(order)!=len(set(order)) or set(order)!=set(requested):raise ValueError('invalid initial query order')
    if any(p['kind'] not in ('native','native_graph') for p in probes):raise ValueError('first qualification supports native probes only')
    for row in config.get('scheduled_jobs',[]):
        if row.get('initial_probe_order',order)!=order:raise ValueError('varying query order needs its own qualification')
    for case in config.get('cases',[]):
        if case.get('initial_probe_order',order)!=order:raise ValueError('varying case query order needs its own qualification')
    def domain(value):
        if isinstance(value,dict):
            out={}
            for k,v in value.items():
                if k=='source_id':continue
                if k=='url':
                    parsed=urlsplit(v);out[k]={'scheme':parsed.scheme,'port':parsed.port,'path':parsed.path,'query':parsed.query}
                else:out[k]=domain(v)
            return out
        if isinstance(value,list):return [domain(v) for v in value]
        return value
    keys=('native_application','application','native_roles','observe_configuration_bindings','discover_dependencies',
          'verification','monitor_execution_safety','continuous_workload','traffic_process','traffic_gc_enabled',
          'dispatch_delay_floor_seconds','native_database_probe_protocol','native_metrics_probe_protocol',
          'native_redis_observation_protocol','baseline_reference_protocol','baseline_warmup_seconds',
          'baseline_consecutive_windows','baseline_health_timeout','synchronized_baseline_rollout_roles',
          'observation_budget','total_probe_budget')
    case_keys=('offered_rps','peer_rps','baseline_settle_seconds','fault_settle_seconds')
    variants=sorted({json.dumps({k:c.get(k) for k in case_keys},sort_keys=True) for c in config.get('cases',[])})
    return {'schema':'fixed_native_acquisition_scope_v2','strategy':'none','input_view':'full','runtime':str(Path(runtime).resolve()),
            'settings':{k:copy.deepcopy(config.get(k)) for k in keys},'initial_probe_order':list(order),
            'probes':domain(probes),'workloads':domain(config['workloads']),
            'direct_workloads':domain(config.get('direct_workloads',{})),
            'background_workloads':domain(config.get('background_workloads',{})),
            'inventory_ttl':config['inventory'].get('ttl',30.),
            'inventory_goals':copy.deepcopy(config['inventory'].get('goals',[])),
            'inventory_contract_facts':[{k:v for k,v in f.items() if k!='observed_at'} for f in config['inventory'].get('contract_facts',[])],
            'inventory_relations':[{k:v for k,v in r.items() if k!='observed_at'} for r in config['inventory'].get('relations',[])],
            'timestamp_scope':'Absolute declaration observation times are per-record evidence and retain ordinary freshness checks; declaration contents and TTLs are fixed.',
            'case_measurement_variants':[json.loads(v) for v in variants],
            'plans':copy.deepcopy(config['plans']),
            'entity_bindings':[{k:e.get(k) for k in ('id','role','kind','binding')} for e in config['inventory']['entities']],
            'mandatory_refresh_rule':'Every catalog probe supplying required_variables; repeated reads charged; final GET precedes empirical/budget authorization.'}


def source_scope(row):
    source=row.get('source_provenance',{})
    if not source.get('source_directory') or not source.get('runtime_path'):raise ValueError('source protocol identity missing')
    config=json.loads((Path(source['source_directory'])/'protocol.json').read_text())
    return acquisition_scope(config,source['runtime_path'])


def check_registry(registry,metadata,calibration_groups):
    comparisons=defaultdict(set)
    aliases=defaultdict(list)
    allowed={'train','development','calibration','qualification','prospective'}
    for group,entry in registry.items():
        if entry['partition'] not in allowed or not entry.get('comparison_group'):raise ValueError('invalid partition registry')
        comparisons[entry['comparison_group']].add(entry['partition'])
        aliases[(entry['partition'],entry['comparison_group'])].append(group)
    if any(len(parts)>1 for parts in comparisons.values()):raise ValueError('comparison group crosses partitions')
    if any(len(groups)>1 and partition in ('calibration','qualification') for (partition,_),groups in aliases.items()):
        raise ValueError('independent comparison families cannot be represented by multiple group aliases')
    for partition,groups in [('train',metadata['split']['train']),('development',metadata['split']['development']),('calibration',calibration_groups)]:
        registered={g for g,e in registry.items() if e['partition']==partition}
        if registered!=set(groups):raise ValueError('registered groups differ from fit/calibration identity')
    return registry


def summarize_frozen_decisions(decisions,policy):
    """Equal group/condition/protocol weighting; no threshold selection."""
    grouped=defaultdict(lambda:defaultdict(list))
    for row in decisions:grouped[row['group']][row['condition']].append(row)
    denominator=len(grouped);covered=recovery=harm=policy_harm=policy_recovery=0.
    selected_groups=set();support_ids=set()
    def bounds(outcome):
        n=outcome.get('attempts',0)
        return ((outcome['successes']/n,(outcome['successes']+outcome['unknown'])/n) if n else (0.,1.))
    for group,conditions in grouped.items():
        for cells in conditions.values():
            for row in cells:
                weight=1/denominator/len(conditions)/len(cells)
                selected=row.get('score') is not None and row['score']>=policy.min_utility and row.get('has_actions',False)
                outcomes=row['outcomes'] if selected else row.get('no_action_outcomes',{})
                success_lo,_=bounds(outcomes.get('business_recovered',{}))
                safe_lo,_=bounds(outcomes.get('non_target_safe',{}))
                policy_recovery+=weight*success_lo;policy_harm+=weight*(1-safe_lo)
                if selected:
                    covered+=weight;recovery+=weight*success_lo;harm+=weight*(1-safe_lo)
                    selected_groups.add(group);support_ids.update(row.get('matching_attempt_ids',[]))
    conditional_recovery=recovery/covered if covered else None;conditional_harm=harm/covered if covered else None
    supported=(covered>0 and len(selected_groups)>=2 and len(support_ids)>=5
               and conditional_recovery>=policy.min_success_probability
               and conditional_harm<=policy.max_harm_probability and policy_harm<=policy.max_harm_probability)
    return {'status':'passed' if supported else 'not_qualified','coverage':covered,'decision_groups':denominator,
            'selected_groups':sorted(selected_groups),'selected_attempt_ids':sorted(support_ids),
            'selected_recovery_lower':conditional_recovery,'selected_harm_upper':conditional_harm,
            'whole_policy_harm_upper':policy_harm if denominator else None,
            'whole_policy_recovery_lower':policy_recovery if denominator else None,
            'weighting':'equal group, then condition, then protocol; unknown-inclusive empirical bounds',
            'threshold_search_performed':False}


def assess_fixed_selector(rows,predictor,policy,registry):
    ledger=getattr(predictor,'admission',None)
    if not ledger or not ledger.get('observation_scope'):raise ValueError('scope-bound candidate admission required')
    if predictor.metadata.get('input_view','full')!='full':raise ValueError('first fixed qualification requires full view')
    check_registry(registry,predictor.metadata,ledger['calibration_groups'])
    scope=ledger['observation_scope'];expected={g for g,e in registry.items() if e['partition']=='qualification'}
    if not expected or {group_key(r) for r in rows}!=expected:raise ValueError('missing or foreign qualification groups')
    if len({r['episode_id'] for r in rows})!=len(rows):raise ValueError('duplicate qualification attempt')
    source_config=json.loads((Path(rows[0]['source_provenance']['source_directory'])/'protocol.json').read_text())
    provider=LiveProvider.__new__(LiveProvider)
    provider.probes=[Probe(**p) for p in source_config['inventory']['probes'] if p.get('phase','pre')=='pre']
    projected=[];eligibility=[];source_cache={}
    for row in rows:
        if row['source']!='live':raise ValueError('native qualification needs live records')
        graph,plan=validate_record(row)
        if registry[group_key(row)]['comparison_group']!=row['application']+'/'+row['comparison_group']:
            raise ValueError('attempt comparison group differs from registry')
        source=row.get('source_provenance',{}).get('source_directory')
        if source not in source_cache:source_cache[source]=source_scope(row)
        if source_cache[source]!=scope:raise ValueError('qualification observation protocol differs from calibration')
        reasons=[];budget=row.get('total_probe_budget',{});limits=scope['settings'].get('total_probe_budget') or {}
        if row.get('restore',{}).get('status')!='restored':reasons.append('independent_reset_not_verified')
        if not isinstance(budget.get('decision_boundary'),dict):reasons.append('recorded_selector_boundary_missing')
        if not limits or budget.get('limits')!=limits or budget.get('actual_runtime_exceeded') is not False:
            reasons.append('missing_or_different_total_query_budget')
        else:
            if budget.get('calls',float('inf'))>limits['max_calls'] or budget.get('cost',float('inf'))>limits['max_cost']:
                reasons.append('query_budget_exceeded')
        events=budget.get('events')
        if not isinstance(events,list):
            reasons.append('query_event_trace_missing')
        else:
            started=[e for e in events if e.get('started') is True]
            measured_cost=sum(e.get('declared_cost',0) for e in started)
            if len(started)!=budget.get('calls') or measured_cost!=budget.get('cost'):
                reasons.append('query_trace_totals_differ')
            if (any(e.get('status')!='observed' or not number(e.get('seconds')) or e['seconds']<0
                    or not number(e.get('declared_timeout')) or e['seconds']>e['declared_timeout'] for e in events)
                    or any(e.get('phase') not in ('initial','mandatory') for e in events)):
                reasons.append('query_trace_incomplete_or_foreign_strategy')
            if [e.get('probe_id') for e in events if e.get('phase')=='initial']!=scope['initial_probe_order']:
                reasons.append('initial_query_sequence_differs')
            try:
                journal=Path(row['artifact_directory'])/'registered_probe_events.jsonl'
                latest={}
                for line in journal.read_text().splitlines():
                    event=json.loads(line);latest[event['sequence']]=event
                if [latest[k] for k in sorted(latest)]!=events:raise ValueError('probe trace differs')
            except (KeyError,OSError,ValueError):reasons.append('raw_query_journal_missing_or_different')
        verification=row.get('outcome',{}).get('verification',{})
        if not row.get('assigned_method') or not verification.get('during_strategy_non_target'):
            reasons.append('initial_acquisition_safety_not_verified')
        if graph is None:reasons.append('missing_input')
        elif len(plan.actions)>1:raise ValueError('first fixed qualification supports single actions only')
        elif plan.actions:
            refreshes=row.get('execution',{}).get('refreshes',[])
            checks=budget.get('pre_write_authorization_checks',[])
            if len(refreshes)!=1 or not checks or not all(c.get('approved') and c.get('elapsed_seconds',float('inf'))<=limits.get('max_seconds',0) for c in checks):
                reasons.append('missing_or_failed_mandatory_budget_check')
            else:
                fresh=EvidenceGraph.from_dict(refreshes[0]['graph'])
                required=provider.refresh_probes(fresh,plan.actions[0])
                mandatory=[e for e in (events or []) if e.get('phase')=='mandatory']
                measured=refreshes[0].get('observation_cost',{})
                measured_probes=measured.get('probes',[])
                if ([e.get('probe_id') for e in mandatory]!=[p.id for p in required]
                        or [p.get('id') for p in measured_probes]!=[p.id for p in required]
                        or any(e.get('declared_cost')!=p.cost or e.get('declared_timeout')!=p.timeout for e,p in zip(mandatory,required))
                        or any(v.get('cost')!=p.cost for v,p in zip(measured_probes,required))
                        or measured.get('registered_probe_cost')!=sum(p.cost for p in required)):
                    reasons.append('mandatory_probe_trace_missing_or_different')
                ranked=rank_candidates(fresh,[plan],policy_for_record(row,policy),predictor)
                if not ranked[0]['executable']:reasons.append('mandatory_empirical_recheck_would_refuse')
        elif budget.get('elapsed_through_close_seconds',float('inf'))>limits.get('max_seconds',0):
            reasons.append('no_action_acquisition_deadline_unknown_or_exceeded')
        copy_row=copy.deepcopy(row)
        if reasons:
            # Keep every attempt in the denominator. This eligibility projection
            # is not written back over its independently collected outcome.
            copy_row.setdefault('outcome',{}).setdefault('effects',{})
            for key in ('business_recovered','non_target_safe'):copy_row['outcome']['effects'][key]=None
        projected.append(copy_row);eligibility.append({'attempt_id':row['episode_id'],'reasons':reasons})
    # Probe URLs belong to source configurations; fixed none replay never sends
    # a query. Reuse complete original descriptors rather than normalized URLs.
    def decide(graph,plans,local_policy,anchor):
        trace=anchor.get('total_probe_budget',{});boundary=trace.get('decision_boundary')
        if not isinstance(boundary,dict) or any(not number(boundary.get(k)) or boundary[k]<0 for k in ('calls','cost','probe_seconds','elapsed_seconds')):
            return {'decision':'abstain','selected_plan':None,'candidates':[],'reason':'recorded_selector_boundary_missing'}
        initial=[e for e in trace.get('events',[]) if e.get('phase')=='initial' and e.get('started')]
        if boundary['calls']!=len(initial) or boundary['cost']!=sum(e['declared_cost'] for e in initial):
            return {'decision':'abstain','selected_plan':None,'candidates':[],'reason':'recorded_selector_boundary_differs_from_queries'}
        # The same selector, reservation and timed inference path uses actual
        # recorded initial usage plus measured replay inference. No network read.
        account=ProbeAccountant(ObservationBudget(**scope['settings']['total_probe_budget']))
        account.started=time.perf_counter()-boundary['elapsed_seconds']
        account.calls=boundary['calls'];account.cost=boundary['cost'];account.seconds=boundary['probe_seconds']
        account.decision_boundary=copy.deepcopy(boundary)
        def forbidden(*args):raise RuntimeError('fixed qualification cannot perform a real/adaptive query')
        decision=budgeted_decision(graph,plans,local_policy,predictor,provider,forbidden,
            budget=ObservationBudget(**scope['settings']['observation_budget']),account=account,strategy='none',seed=source_config.get('seed',42))
        decision['replay_timing_scope']='Recorded preselector usage plus measured replay inference; prospective runtime variability remains unvalidated.'
        return decision
    decisions=replay_conditions(projected,policy,predictor,protocol_matching=True,decision_function=decide)
    metrics=summarize_frozen_decisions(decisions,policy)
    return {'schema':'fixed_selector_qualification_v2','status':metrics['status'],
            'evidence_kind':'independent_matched_arm_replay','permitted_purpose':'bounded_fixed_policy_validation',
            'strategy':'none','input_view':'full','training_configuration':copy.deepcopy(predictor.metadata),
            'policy':asdict(policy),'registry':copy.deepcopy(registry),'observation_scope':scope,
            'qualification_groups':sorted(expected),'attempts':len(rows),'eligibility':eligibility,'decisions':decisions,
            'metrics':metrics,'limits':'Condition-level replay under repeatability/exchangeability; not live policy performance, production safety, or adaptive strategy validation.'}


def make_bounded_profile(assessment,config,out_directory):
    if assessment.get('schema')!='fixed_selector_qualification_v2' or assessment.get('status')!='passed':raise ValueError('fixed selector not qualified')
    return {'schema':'e2r_intervention_validation_profile_v2','status':'qualified_for_bounded_experiment',
            'purpose':'bounded_fixed_policy_validation','evidence_kind':'independent_matched_arm_replay',
            'training_configuration':copy.deepcopy(assessment['training_configuration']),
            'policy':copy.deepcopy(assessment['policy']),'assessment':copy.deepcopy(assessment),
            'configuration':copy.deepcopy(config),'campaign_output':str(Path(out_directory).resolve())}


def validate_bounded_profile(policy,predictor,profile,config,out_directory,runtime):
    error='v2_requires_scoped_policy_qualification'
    if not isinstance(profile,dict) or profile.get('schema')!='e2r_intervention_validation_profile_v2':return policy,error
    if config is None or out_directory is None:return policy,'bounded_profile_not_valid_for_general_service'
    if (profile.get('status')!='qualified_for_bounded_experiment' or profile.get('purpose')!='bounded_fixed_policy_validation'
            or profile.get('evidence_kind')!='independent_matched_arm_replay'):return policy,error
    if profile.get('training_configuration')!=predictor.metadata or profile.get('policy')!=asdict(policy):return policy,'v2_profile_model_or_policy_mismatch'
    if profile.get('configuration')!=config or profile.get('campaign_output')!=str(Path(out_directory).resolve()):return policy,'v2_profile_campaign_mismatch'
    try:
        assessment=profile['assessment'];ledger=predictor.admission
        if (not ledger or assessment['status']!='passed' or assessment['training_configuration']!=predictor.metadata
                or assessment['policy']!=asdict(policy) or assessment['strategy']!='none' or assessment['input_view']!='full'):
            raise ValueError('inconsistent assessment')
        if (assessment.get('schema')!='fixed_selector_qualification_v2'
                or assessment.get('evidence_kind')!='independent_matched_arm_replay'
                or assessment.get('permitted_purpose')!='bounded_fixed_policy_validation'):
            raise ValueError('assessment purpose differs')
        if ledger.get('min_attempts',0)<5 or ledger.get('min_groups',0)<2:raise ValueError('candidate evidence floor differs')
        if (predictor.metadata['source']!='live' or predictor.metadata.get('input_view','full')!='full'
                or policy.min_success_probability<.8 or policy.max_harm_probability>.05):raise ValueError('unsupported policy scope')
        scope=acquisition_scope(config,runtime)
        if scope!=ledger['observation_scope'] or scope!=assessment['observation_scope']:raise ValueError('scope differs')
        if config.get('monitor_execution_safety') is not True or not config.get('total_probe_budget'):raise ValueError('missing monitoring/budget')
        plans=config['plans']
        if any(len(p['actions'])>1 for p in plans) or config['arms']!=['none']:raise ValueError('only fixed single-action selector supported')
        schedule=config['scheduled_jobs']
        if not 0<len(schedule)<=20 or any(j['arm']!='none' for j in schedule):raise ValueError('unbounded/foreign strategy schedule')
        if len({j['original_schedule_slot'] for j in schedule})!=len(schedule):raise ValueError('duplicate scheduled slot')
        registry=check_registry(assessment['registry'],predictor.metadata,ledger['calibration_groups'])
        for case in config['cases']:
            entry=registry[config['application']+'/'+case['group_id']]
            if entry['partition']!='prospective' or entry['comparison_group']!=config['application']+'/'+case.get('comparison_group',case['group_id']):raise ValueError('prospective group mismatch')
        checked=summarize_frozen_decisions(assessment['decisions'],policy)
        if checked!=assessment['metrics'] or checked['status']!='passed':raise ValueError('qualification summary differs')
    except (KeyError,TypeError,ValueError):return policy,'v2_profile_scope_or_evidence_mismatch'
    return policy,None
