"""Fixed disjoint-arrival reference pools for a prospective no-fault check."""
import math
import time
from dataclasses import asdict
from rca_bench.recovery_oracle import WorkloadWindow
from .schema import number


def pool_requests(cycles,name,start,end,source_id,protocol,require_healthy=True,expected_rate=None):
    windows=[];latencies=[];successes=0;attempts=0
    expected_count=round((end-start)/10)
    if len(cycles)!=expected_count:raise ValueError('fixed reference cohort count differs')
    for index,cycle in enumerate(cycles):
        w,raw=cycle[name];requests=raw['transaction_results']
        rate=raw['offered_rps'] if expected_rate is None else expected_rate
        expected_start=start+index*10
        if (abs(w.start_time-expected_start)>1e-6 or abs(w.end_time-(expected_start+10))>1e-6
                or w.source_id!=source_id or w.samples<protocol.min_samples or type(w.state_canary_ok) is not bool
                or not number(w.measurement_completed_at) or not w.end_time<=w.measurement_completed_at<=time.time()
                or not number(w.p95_ms) or w.p95_ms<=0 or not number(w.throughput_rps) or w.throughput_rps<0
                or not number(w.error_rate) or not 0<=w.error_rate<=1
                or require_healthy and (w.state_canary_ok is not True or w.throughput_rps<=0 or w.error_rate>.01)):
            raise ValueError('unavailable fixed reference constituent')
        observed=sorted(r['latency_ms']+1000*max(0,r['dispatch_delay_seconds']) for r in requests)
        yes=sum(r['ok'] is True for r in requests)
        if (len(requests)!=w.samples or len(requests)!=raw['attempts'] or yes!=raw['successes']
                or raw['offered_rps']!=rate or raw['target_window_seconds']!=10 or len(requests)!=round(rate*10)
                or observed!=raw['latencies_ms'] or any(not number(x) or x<0 for x in observed)
                or not math.isclose(w.error_rate,(len(requests)-yes)/len(requests),abs_tol=1e-12)
                or not math.isclose(w.throughput_rps,yes/10,abs_tol=1e-12)
                or w.p95_ms!=observed[math.ceil(.95*len(observed))-1]):
            raise ValueError('raw requests and cohort statistics differ')
        latencies.extend(observed);successes+=yes;attempts+=len(requests);windows.append(w)
    latencies.sort()
    pooled=WorkloadWindow(attempts,successes/(end-start),(attempts-successes)/attempts,
        latencies[math.ceil(.95*attempts)-1],all(w.state_canary_ok is True for w in windows),'raw-pool-'+str(start)+'-'+str(end)+'-'+source_id,
        source_id,start,end,max(w.measurement_completed_at for w in windows))
    return pooled,{'member_windows':[w.window_id for w in windows],'attempts':attempts,'successes':successes,
                   'duration_seconds':end-start,'statistic':'nearest-rank p95 of all dispatch-inclusive raw requests',
                   'window':asdict(pooled)}


def bidirectional_latency(left,right,protocol):
    forward=left.p95_ms<=max(right.p95_ms*protocol.latency_multiplier,protocol.latency_floor_ms)
    reverse=right.p95_ms<=max(left.p95_ms*protocol.latency_multiplier,protocol.latency_floor_ms)
    return {'status':'pass' if forward and reverse else 'fail','left_p95_ms':left.p95_ms,'right_p95_ms':right.p95_ms,
            'left_within_right':forward,'right_within_left':reverse}


def all_arrival_windows(baseline,windows,protocol,start,end,evaluated_at):
    """Retrospective validity on disjoint arrival cohorts, not an action oracle.

    Pool calculation can finish milliseconds after its nominal end. Preserve
    that timestamp; membership rather than a fabricated timestamp separates
    the reference from the subsequent arrival cohorts.
    """
    rows=[];previous=start;seen={baseline.window_id}
    for w in windows:
        valid=(w.source_id==baseline.source_id and w.window_id not in seen and w.samples>=protocol.min_samples
               and abs(w.start_time-previous)<1e-6 and w.start_time>=baseline.end_time
               and abs(w.end_time-w.start_time-10)<1e-6
               and all(number(v) for v in (w.start_time,w.end_time,w.measurement_completed_at,w.p95_ms,w.throughput_rps,w.error_rate))
               and w.start_time<w.end_time<=w.measurement_completed_at<=evaluated_at
               and w.p95_ms>=0 and w.throughput_rps>=0 and 0<=w.error_rate<=1 and type(w.state_canary_ok) is bool)
        okay=(valid and w.state_canary_ok is True and w.error_rate<=baseline.error_rate+protocol.error_margin
              and w.p95_ms<=max(baseline.p95_ms*protocol.latency_multiplier,protocol.latency_floor_ms)
              and w.throughput_rps>=baseline.throughput_rps*protocol.throughput_fraction)
        rows.append({'window_id':w.window_id,'valid':valid,'status':'pass' if okay else 'fail' if valid else 'unknown'})
        previous=w.end_time;seen.add(w.window_id)
    complete=len(windows)==round((end-start)/10) and abs(previous-end)<1e-6
    status='pass' if complete and all(r['status']=='pass' for r in rows) else 'fail'
    return {'status':status,'complete_fixed_interval':complete,'windows':rows,
            'scope':'Every fixed disjoint arrival cohort; retrospective no-fault validity, not post-action recovery.',
            'baseline_measurement_completed_at':baseline.measurement_completed_at,'evaluated_at':evaluated_at}
