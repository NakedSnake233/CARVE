"""Read-only local cgroup observations for owned native application processes.

These samples never enter action selection, warm the service or shift verification.
"""
import json,re,threading,time
from pathlib import Path


def read_cpu(directory):
    directory=Path(directory)
    values={k:int(v) for k,v in (line.split() for line in (directory/'cpu.stat').read_text().splitlines())}
    quota,period=(directory/'cpu.max').read_text().split()
    return {'cpu':values,'quota_usec':None if quota=='max' else int(quota),'period_usec':int(period),
            'memory_bytes':int((directory/'memory.current').read_text())}


def cgroup_directory(pod,container,root='/sys/fs/cgroup'):
    uid=pod['metadata']['uid'];identity=container['containerID'].removeprefix('containerd://')
    if not re.fullmatch(r'[0-9a-f-]{36}',uid) or not re.fullmatch(r'[0-9a-f]{64}',identity):raise ValueError('unexpected local cgroup identity')
    qos=pod['status'].get('qosClass','').lower()
    if qos not in ('burstable','besteffort','guaranteed'):raise ValueError('missing pod qos')
    base=Path(root)/'kubepods.slice'
    if qos!='guaranteed':base=base/f'kubepods-{qos}.slice'
    prefix='kubepods' if qos=='guaranteed' else f'kubepods-{qos}'
    return base/f"{prefix}-pod{uid.replace('-','_')}.slice"/f'cri-containerd-{identity}.scope'


class StartupSampler:
    def __init__(self,client,directory,names,*,interval=.5,discovery_interval=2.):
        self.client=client;self.directory=Path(directory);self.names=set(names);self.interval=interval;self.discovery_interval=discovery_interval
        self.stop_event=threading.Event();self.thread=None;self.samples=0;self.errors=[]

    def start(self):
        self.thread=threading.Thread(target=self._run,name='native-startup-cgroup',daemon=True);self.thread.start();return self

    def _run(self):
        known=[];discover_at=0.
        with (self.directory/'startup_cgroup_samples.jsonl').open('x') as out:
            while not self.stop_event.is_set():
                try:
                    if time.monotonic()>=discover_at:
                        known=[]
                        for pod in self.client.list('Pod'):
                            if pod['metadata'].get('namespace')!=self.client.namespace or pod['metadata'].get('labels',{}).get('app') not in self.names:continue
                            for c in pod.get('status',{}).get('containerStatuses',[]):
                                if not c.get('containerID') or not c.get('state',{}).get('running'):continue
                                path=cgroup_directory(pod,c)
                                known.append(({'pod':pod['metadata']['name'],'uid':pod['metadata']['uid'],'role':pod['metadata']['labels']['app'],
                                    'container_id':c['containerID'],'started_at':c['state']['running']['startedAt'],'ready':c.get('ready'),
                                    'pod_conditions':pod['status'].get('conditions',[]),'metadata_observed_at':time.time()},path))
                        discover_at=time.monotonic()+self.discovery_interval
                    for meta,path in known:
                        try:
                            row={**meta,'observed_at':time.time(),**read_cpu(path)};out.write(json.dumps(row)+'\n');self.samples+=1
                        except FileNotFoundError:pass # pods can disappear normally during rollout
                    out.flush()
                except Exception as e:
                    error={'at':time.time(),'type':type(e).__name__,'detail':str(e)[:300]};self.errors.append(error);out.write(json.dumps({'sampling_error':error})+'\n');out.flush()
                self.stop_event.wait(self.interval)

    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(35)
        stopped=self.thread is None or not self.thread.is_alive()
        result={'status':'complete' if stopped and self.samples and not self.errors else 'incomplete','samples':self.samples,'errors':self.errors,
            'scope':'Read-only host cgroup counters and pod readiness metadata; no service requests or decision input; intervals may span rollout/termination.'}
        (self.directory/'STARTUP_DIAGNOSTICS.json').write_text(json.dumps(result,indent=2)+'\n')
        if not stopped:raise RuntimeError('startup sampler did not stop')
        return result
