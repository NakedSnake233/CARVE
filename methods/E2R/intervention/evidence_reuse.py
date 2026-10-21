"""Bounded-age supporting evidence and configuration consistency, not atomic effects."""
import time


def namespace_versions(client):
    return {kind:{obj['metadata']['name']:(obj['metadata']['uid'],obj['metadata']['resourceVersion']) for obj in client.list(kind)} for kind in ('Deployment','Pod','Service')}


def check_reuse(client,graph,before,observations,*,now=None,max_age=10.,required_keys=None,historical_entities=(),relation_sources=()):
    fixed_now=now;started=time.time() if now is None else now;reasons=[];checked=[]
    if not observations:reasons.append('no_registered_observations')
    if any(started<o['completed_at'] or started-o['completed_at']>max_age for o in observations):reasons.append('observation_age')
    keys=set(required_keys) if required_keys is not None else {(f.entity,f.variable) for f in getattr(graph,'facts',[])}
    supports=[];historical=[];deadlines=[]
    for entity,variable in sorted(keys):
        fact=graph.latest(entity,variable);node=graph.entity(entity)
        if fact is None or not fact.usable(started,node.uid):reasons.append('unusable_support:'+entity+':'+variable);continue
        record={'entity':entity,'variable':variable,'fact_id':fact.id,'observed_at':fact.observed_at,'ttl':fact.ttl,'source':fact.source}
        if entity in historical_entities and not node.kind:
            historical.append(record);deadlines.append(fact.observed_at+fact.ttl)
        else:
            supports.append(record);deadlines.append(fact.observed_at+min(max_age,fact.ttl))
    relations=[]
    for r in getattr(graph,'relations',[]):
        if r.source in relation_sources and r.kind=='competes_for':
            relations.append({'source':r.source,'target':r.target,'observed_at':r.observed_at,'ttl':r.ttl,'validity':r.validity})
            if r.origin not in ('observed','configuration','contract') or r.validity=='unknown':reasons.append('unsupported_goal_binding')
            deadlines.append(r.observed_at+min(max_age,r.ttl))
    try:
        after=namespace_versions(client)
        for e in graph.entities:
            if not e.kind:continue
            identity=after.get(e.kind,{}).get(e.name);checked.append({'entity':e.id,'live_identity':identity})
            if identity!=(e.uid,e.resource_version):reasons.append('entity_version_changed:'+e.id)
        if before!=after:reasons.append('namespace_version_changed')
    except Exception as error:
        after=None;reasons.append('version_read_failed:'+type(error).__name__)
    finished=time.time() if fixed_now is None else fixed_now
    deadlines += [o['completed_at']+max_age for o in observations]
    if any(finished>deadline for deadline in deadlines):reasons.append('support_age_expired')
    return {'valid':not reasons,'reasons':sorted(set(reasons)),'checked_entities':checked,'namespace_versions_after':after,'checked_at':finished,
            'supporting_facts':supports,'historical_template_facts':historical,'supporting_bindings':relations,
            'latest_allowed_write_at':min(deadlines) if deadlines else started,'max_observation_age_seconds':max_age,
            'scope':'Actual timestamps of action/goal supporting live evidence, with separately declared historical-template TTL; checked UID/RV consistency. Goal definitions are requirements, not fresh observations. Unobserved load/data changes and cross-object races remain outside this certificate.'}
