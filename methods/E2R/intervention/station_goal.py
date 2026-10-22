"""One-row owned Station fixture plus real native read acknowledgement; no table reset."""
import json,re,subprocess,time,uuid
from pathlib import Path
from .campaign import append_json,http_request

OWNER='e2r-intervention-native-20260929'
STORES=('shared-db','private-db')

def validate_station(goal):
    if set(goal)!={'id','name','stay_time'} or str(uuid.UUID(goal['id']))!=goal['id']:
        raise ValueError('canonical station UUID required')
    if not re.fullmatch(r'e2r_ack_[a-z0-9_]{1,100}',goal['name']) or type(goal['stay_time']) is not int or not 1<=goal['stay_time']<=60:
        raise ValueError('bounded owned station fixture required')
    return goal

def goal_step(goal):
    validate_station(goal)
    return {'path':'/api/v1/stationservice/stations/namelist','method':'POST','body':[goal['id']],
            'success_path':['status'],'success_value':1,'expected_data_items':[goal['name']],'station_goal_check':True}

def strip_goal(spec):
    spec.pop('continuity_check',None)
    if 'steps' in spec:spec['steps']=[s for s in spec['steps'] if not s.get('station_goal_check')]
    return spec

class StationGoalLease:
    def __init__(self,client,owner,goal,directory):
        self.client=client;self.owner=owner;self.goal=validate_station(goal);self.directory=Path(directory);self.armed=False;self.closed=False;self.before={};self.uids={}
        if client.namespace not in {'e2r-isg-trainticket',*[f'e2r-isg-trainticket-p{i}' for i in (2,3,4)]} or owner not in STORES:raise ValueError('unowned station scope')
        labels=client._call(['get','namespace',client.namespace,'-o','json'])['metadata'].get('labels',{})
        if labels.get('e2r-owner')!=OWNER:raise ValueError('namespace ownership missing')
        for store in STORES:self.uids[store]=client.get('Deployment',store)['metadata']['uid']

    def sql(self,store,statement):
        if store not in STORES or self.client.get('Deployment',store)['metadata']['uid']!=self.uids[store]:raise ValueError('database identity changed')
        command=['kubectl','--namespace',self.client.namespace,'exec','-i','deployment/'+store,'--','sh','-c','MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot -N -B ts']
        result=subprocess.run(command,input=statement,env=self.client.context.environment(),capture_output=True,text=True,timeout=20,check=False)
        if result.returncode:raise RuntimeError('scoped station SQL failed with exit '+str(result.returncode))
        return [line.split('\t') for line in result.stdout.splitlines() if line]

    def table(self,store):return self.sql(store,'SELECT id,name,stay_time FROM station ORDER BY id;')

    def prepare(self,business):
        g=self.goal;self.before={store:self.table(store) for store in STORES}
        if any(r[0]==g['id'] or r[1]==g['name'] for rows in self.before.values() for r in rows):raise ValueError('fixture collision; no insert or cleanup ownership')
        append_json(self.directory/'station_goal_lifecycle.jsonl',{'event':'insert_intent','goal':g,'owner':self.owner,'database_uids':self.uids,'before_rows':self.before,'time':time.time()})
        self.armed=True
        self.sql(self.owner,f"INSERT INTO station (id,name,stay_time) VALUES ('{g['id']}','{g['name']}',{g['stay_time']});")
        after=self.table(self.owner);expected=sorted([*self.before[self.owner],[g['id'],g['name'],str(g['stay_time'])]])
        if after!=expected:raise ValueError('station insertion not exactly acknowledged')
        spec={'url':business['url'],'timeout':business.get('timeout',3),'steps':[
            {'path':'/api/v1/stationservice/stations/idlist','method':'POST','body':[g['name']],'success_path':['status'],'success_value':1,'expected_data_items':[g['id']],'station_goal_check':True},goal_step(g)]}
        detail={};ok,latency=http_request(spec,detail)
        receipt={'status':'acknowledged' if ok else 'failed','required':g,'owner_database':self.owner,'database_uids':self.uids,'fixture_source':'one-row evaluator SQL seed followed by native HTTP read acknowledgement','native_readback':detail,'latency_ms':latency,'completed_at':time.time()}
        append_json(self.directory/'station_goal_lifecycle.jsonl',{'event':'native_acknowledgment','receipt':receipt})
        if not ok:raise ValueError('native Station did not acknowledge fixture')
        return receipt

    def cleanup(self):
        if not self.armed or self.closed:return
        g=self.goal;rows=self.table(self.owner);matching=[r for r in rows if r[0]==g['id'] or r[1]==g['name']]
        exact=[g['id'],g['name'],str(g['stay_time'])]
        if matching and matching!=[exact]:raise ValueError('owned marker changed; refuse deletion')
        append_json(self.directory/'station_goal_lifecycle.jsonl',{'event':'delete_intent','goal':g,'observed_rows':matching,'time':time.time()})
        if matching:self.sql(self.owner,f"DELETE FROM station WHERE id='{g['id']}' AND name='{g['name']}' AND stay_time={g['stay_time']};")
        after={store:self.table(store) for store in STORES};good=after==self.before
        receipt={'status':'restored' if good else 'failed','before_rows':self.before,'after_rows':after,'only_owned_marker_deleted':True,'marker_present_before_cleanup':bool(matching),'deleted_owned_row':bool(matching),'database_uids':self.uids,'completed_at':time.time()}
        (self.directory/'station_goal_cleanup.json').write_text(json.dumps(receipt,indent=2)+'\n')
        append_json(self.directory/'station_goal_lifecycle.jsonl',{'event':'cleanup_verified','receipt':receipt})
        if not good:raise ValueError('station tables differ from before fixture; no table overwrite attempted')
        self.closed=True
