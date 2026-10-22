from dataclasses import replace
import math
import pytest
from rca_bench.recovery_oracle import WorkloadWindow
from methods.E2R.intervention.reference_validity import pool_requests,bidirectional_latency,all_arrival_windows
from methods.E2R.intervention.verification import VerificationProtocol


def cycles(start=60.,count=6,failed=0):
    result=[]
    for i in range(count):
        values=[3000.]*failed+[30. if i==count-1 else 1.]*(100-failed);values.sort()
        rows=[{'ok':j>=failed,'latency_ms':3000. if j<failed else 30. if i==count-1 else 1.,'dispatch_delay_seconds':0.} for j in range(100)]
        w=WorkloadWindow(100,(100-failed)/10,failed/100,values[94],True,str(i),'peer',start+10*i,start+10*i+10,start+10*i+10+.001)
        raw={'transaction_results':rows,'attempts':100,'successes':100-failed,'latencies_ms':values,'offered_rps':10.,'target_window_seconds':10}
        result.append({'peer':(w,raw)})
    return result


def protocol():return VerificationProtocol(latency_multiplier=1.5,latency_floor_ms=17.888549715280533,error_margin=.02)


def test_fixed_pool_uses_all_raw_requests_not_average_window_quantiles():
    data=cycles(failed=1);pooled,receipt=pool_requests(data,'peer',60.,120.,'peer',protocol())
    assert pooled.samples==600 and pooled.p95_ms==30. and pooled.throughput_rps==9.9 and pooled.error_rate==.01
    assert receipt['successes']==594 and len(receipt['member_windows'])==6
    data[0]['peer'][1]['transaction_results'].pop()
    with pytest.raises(ValueError,match='statistics differ'):pool_requests(data,'peer',60.,120.,'peer',protocol())


def test_reference_never_skips_unavailable_or_noncontiguous_constituents():
    bad=cycles(failed=2)
    with pytest.raises(ValueError,match='unavailable'):pool_requests(bad,'peer',60.,120.,'peer',protocol())
    # Holdout still retains failures; downstream all-window checks judge them.
    pooled,_=pool_requests(bad,'peer',60.,120.,'peer',protocol(),require_healthy=False)
    assert pooled.error_rate==.02 and pooled.samples==600
    bad=cycles();w,raw=bad[2]['peer'];bad[2]['peer']=(replace(w,start_time=w.start_time+1),raw)
    with pytest.raises(ValueError,match='unavailable'):pool_requests(bad,'peer',60.,120.,'peer',protocol())


def test_bidirectional_gate_rejects_inflated_reference_even_if_later_faster():
    pool,_=pool_requests(cycles(),'peer',60.,120.,'peer',protocol())
    assert bidirectional_latency(replace(pool,p95_ms=100),replace(pool,p95_ms=10),protocol())['status']=='fail'
    assert bidirectional_latency(replace(pool,p95_ms=7),replace(pool,p95_ms=8),protocol())['status']=='pass'


def test_all_windows_gate_cannot_hide_early_failure_with_final_three_passes():
    reference,_=pool_requests(cycles(),'peer',60.,120.,'peer',protocol())
    data=cycles(start=120.,count=6);windows=[v['peer'][0] for v in data]
    windows[0]=replace(windows[0],p95_ms=100.)
    checked=all_arrival_windows(reference,windows,protocol(),120.,180.,200.)
    assert checked['status']=='fail' and checked['windows'][0]['status']=='fail'
    assert all(v['status']=='pass' for v in checked['windows'][-3:])
    # Pool completion is truthfully after120; separate arrival membership still works.
    assert reference.measurement_completed_at>120 and checked['windows'][1]['valid']
