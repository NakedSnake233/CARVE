"""Conservative observed resource bounds for the study's single-node Kubernetes cluster."""
import math


def cpu_cores(value):
    text=str(value)
    for suffix,multiplier in [('n',1e-9),('u',1e-6),('m',1e-3)]:
        if text.endswith(suffix):return float(text[:-1])*multiplier
    return float(text)


def memory_bytes(value):
    text=str(value)
    for suffix,multiplier in [('Ki',1024),('Mi',1024**2),('Gi',1024**3),('Ti',1024**4),
                             ('k',1000),('M',1000**2),('G',1000**3),('T',1000**4)]:
        if text.endswith(suffix):return float(text[:-len(suffix)])*multiplier
    return float(text)


def requests(spec,resource,parse):
    # Summing init and ordinary containers is a conservative upper bound on
    # requests, including restartable init containers. Native study pods have no
    # init containers; no claim of reproducing every scheduler plugin is made.
    amount=sum(parse(c.get('resources',{}).get('requests',{}).get(resource,0))
               for c in spec.get('containers',[])+spec.get('initContainers',[]))
    amount=max(amount,parse(spec.get('resources',{}).get('requests',{}).get(resource,0)))
    return amount+parse(spec.get('overhead',{}).get(resource,0))


def scheduler_slots(nodes,pods,namespace,quota,template,committed_cpu):
    if len(nodes)!=1:return None
    node=nodes[0];conditions={c['type']:c['status'] for c in node.get('status',{}).get('conditions',[])}
    if conditions.get('Ready')!='True':return None
    if node.get('spec',{}).get('unschedulable') or any(conditions.get(k)=='True' for k in ('MemoryPressure','DiskPressure','PIDPressure')):return 0
    active=[p for p in pods if p.get('status',{}).get('phase') not in ('Succeeded','Failed')]
    alloc=node['status']['allocatable'];bounds=[int(alloc['pods'])-len(active)]
    for resource,parse in [('cpu',cpu_cores),('memory',memory_bytes)]:
        unit=requests(template['spec'],resource,parse)
        free=parse(alloc[resource])-sum(requests(p['spec'],resource,parse) for p in active)
        if unit>0:bounds.append(math.floor(max(0,free)/unit))
    hard=quota['spec']['hard'];used=quota.get('status',{}).get('used',{})
    for resource,parse in [('cpu',cpu_cores),('memory',memory_bytes)]:
        key='limits.'+resource
        if key in hard:
            unit=sum(parse(c.get('resources',{}).get('limits',{}).get(resource,0)) for c in template['spec']['containers'])
            committed=parse(used.get(key,0))
            if resource=='cpu':committed=max(committed,committed_cpu)
            if unit>0:bounds.append(math.floor(max(0,parse(hard[key])-committed)/unit))
    if 'pods' in hard:
        ns_count=sum(p['metadata']['namespace']==namespace for p in active)
        bounds.append(int(hard['pods'])-max(ns_count,int(used.get('pods',0))))
    return max(0,min(bounds))
