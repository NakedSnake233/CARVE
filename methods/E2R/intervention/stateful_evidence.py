"""Measured state-continuity goal for isolated Cart intervention evidence."""
import copy
import json
from pathlib import Path
from .cart_transaction import seed_acknowledged_cart
from .evidence_campaign import NativeFixedEvidenceBackend
from .campaign import collector,KubernetesBackend
from .station_goal import StationGoalLease,goal_step,strip_goal

def validate_state_goal(config):
    state=config.get('state_continuity',{});business=config.get('workloads',{}).get('business',{})
    if state.get('owner_database') not in ('shared-db','private-db'):raise ValueError('registered state owner required')
    if config.get('native_application')=='boutique':
        if not state.get('required_cart') or business.get('continuity_check')!=state['required_cart'] or business.get('canary',{}).get('continuity_check')!=state['required_cart']:raise ValueError('identical registered cart goals required')
    elif config.get('native_application')=='trainticket':
        expected=goal_step(state['required_station'])
        if state.get('protocol')!='station_owned_fixture_native_readback_v1' or any([s for s in spec.get('steps',[]) if s.get('station_goal_check')]!=[expected] for spec in (business,business.get('canary',{}))):raise ValueError('identical registered native Station goal steps required')
    else:raise ValueError('unsupported native state goal application')


class StatefulEvidenceBackend(NativeFixedEvidenceBackend):
    observation_method='assigned_state_continuity_evidence'

    def __init__(self,config,out_dir):
        validate_state_goal(config)
        super().__init__(config,out_dir)
        self.continuity_receipt=None

    def start(self,case,directory):
        self.continuity_receipt=None
        self.station_goal_lease=None
        self.startup_sampler=None
        if self.config.get('startup_diagnostics'):
            from .startup_diagnostics import StartupSampler
            names=[e['name'] for e in self.config['inventory']['entities'] if e.get('kind')=='Deployment']
            self.startup_sampler=StartupSampler(self.client,directory,names).start()
        return super().start(case,directory)

    def prepare_workload_state(self,case):
        new=next(e for e in self.config['inventory']['entities'] if e['id']=='new')
        actual=self.client.get('Deployment',new['name'])
        owner=self.provider.configured_database(actual['spec']['template'])
        entry=next(e for e in self.config['inventory']['entities'] if e['id']=='entry')
        service=self.client.get('Service',entry['name']);selector=service['spec'].get('selector',{})
        labels=actual['spec']['template']['metadata']['labels']
        if owner!=self.config['state_continuity']['owner_database'] or not selector or not all(labels.get(k)==v for k,v in selector.items()):
            raise ValueError('acknowledged-state owner/routing not observed as registered')
        if (self.provider.ready_endpoints(entry['name'],selector) or 0)<1:
            raise ValueError('active route has not converged before state acknowledgment')
        store=next(e for e in self.config['inventory']['entities'] if e.get('kind')=='Deployment' and e['role']=='shared_dependency' and e['name']==owner)
        store_deployment=self.client.get('Deployment',store['name']);store_service=self.client.get('Service',store['name'])
        store_selector=store_service['spec'].get('selector',{});store_labels=store_deployment['spec']['template']['metadata']['labels']
        owner_edges=[r for r in self.config['inventory']['relations'] if r['source']=='ack_state' and r['kind']=='depends_on']
        if (len(owner_edges)!=1 or owner_edges[0]['target']!=store['id'] or not store_selector
                or not all(store_labels.get(k)==v for k,v in store_selector.items())
                or (self.provider.ready_endpoints(store['name'],store_selector) or 0)<1):
            raise ValueError('state-owner graph/store Service identity mismatch before acknowledgment')
        if self.config['native_application']=='boutique':
            receipt=seed_acknowledged_cart(self.config['workloads']['business'],self.config['state_continuity']['required_cart'])
        else:
            self.station_goal_lease=StationGoalLease(self.client,owner,self.config['state_continuity']['required_station'],self.current_dir)
            receipt=self.station_goal_lease.prepare(self.config['workloads']['business'])
        receipt.update(owner_deployment_uid=store_deployment['metadata']['uid'],owner_service_uid=store_service['metadata']['uid'])
        receipt.update(owner_database=owner,active_deployment_uid=actual['metadata']['uid'],entry_service_uid=service['metadata']['uid'])
        (Path(self.current_dir)/'acknowledged_state_before_fault.json').write_text(json.dumps(receipt,indent=2)+'\n')
        self.continuity_receipt=receipt

    def verify(self,graph,plan,execution,completed_at):
        outcome=super().verify(graph,plan,execution,completed_at)
        outcome['acknowledged_state_goal']={'preincident_receipt':self.continuity_receipt,
            'post_window_state_canaries':[w['state_canary_ok'] for w in outcome['verification']['business']['raw_windows']],
            'scope':'Business success requires the same acknowledged native state goal, in addition to existing error/throughput/latency checks.'}
        return outcome

    def prepare_reset_health(self):
        # Original pre-study routing can use a different store than this trial.
        # Outcome/continuity verification has already finished. Cleanup tests
        # ordinary original-endpoint health with newly started traffic only.
        # Drain/record traffic here; leave native application-state reset to
        # the existing single final close, so its receipt is not overwritten.
        KubernetesBackend.close_traffic(self)
        workloads={}
        for name,old in self.workloads.items():
            spec=copy.deepcopy(old.spec);spec['offered_rps']=old.offered_rps
            if name=='business':
                strip_goal(spec);strip_goal(spec['canary'])
            workloads[name]=collector(spec)
        self.workloads=workloads
        self.start_traffic()

    def restore(self):
        original=self.workloads
        try:
            result=super().restore()
            result['reset_health_scope']='ordinary original-endpoint health after restoring pre-study fields; scientific state-continuity outcomes unchanged'
            return result
        finally:
            if self.workloads is not original:
                try:KubernetesBackend.close_traffic(self)
                finally:self.workloads=original

    def finalize_campaign(self):
        # Registered application cleanup deletes experimental carts. Final
        # environment health then checks ordinary endpoint availability only;
        # all per-attempt continuity outcomes have already been recorded.
        original=self.config;self.config=copy.deepcopy(original)
        strip_goal(self.config['workloads']['business']['canary'])
        try:
            result=super().finalize_campaign()
            result['canary_scope']='ordinary endpoint health after scoped experimental state cleanup; no outcome relabeling'
            (self.out_dir/'campaign_baseline_restore.json').write_text(json.dumps(result,indent=2)+'\n')
            return result
        finally:self.config=original

    def close_traffic(self):
        try:return super().close_traffic()
        finally:
            try:
                if getattr(self,'station_goal_lease',None) and self.traffic is None:
                    self.station_goal_lease.cleanup()
            finally:
                if getattr(self,'startup_sampler',None):self.startup_sampler.stop()

    def stop_reason(self,row):
        if row.get('execution',{}).get('status') in ('execution_unknown','write_deviation'):
            return 'uncertain_native_write_requires_inspection_before_next_slot'
        return None
