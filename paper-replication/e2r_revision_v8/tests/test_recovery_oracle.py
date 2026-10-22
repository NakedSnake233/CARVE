from rca_bench.recovery_oracle import WorkloadWindow,evaluate_recovery


BASE=WorkloadWindow(samples=100,throughput_rps=10,error_rate=.01,p95_ms=100,
                    state_canary_ok=True,window_id='baseline',source_id='workload-a',
                    start_time=0,end_time=10)
TECH={'fault_absent':True,'resource_restored':True,'ready_endpoints':True}


def windows(*,throughput=9,error=.015,p95=110,canary=True):
    return [WorkloadWindow(samples=30,throughput_rps=throughput,error_rate=error,
            p95_ms=p95,state_canary_ok=canary,window_id=f'post-{index}',
            source_id='workload-a',start_time=21+index*10,end_time=30+index*10)
            for index in range(3)]


def evaluate(post,**kwargs):
    return evaluate_recovery(baseline=BASE,post_windows=post,technical=TECH,
        repair_completed_at=20,evaluated_at=52,**kwargs)


def test_three_passing_windows_and_technical_checks_are_required():
    result=evaluate(windows(),require_state_canary=True)
    assert result['status']=='PASS' and result['success']
    result=evaluate(windows()[:2])
    assert result['status']=='UNKNOWN' and not result['success']


def test_failed_or_unmeasurable_state_never_counts_as_success():
    bad=windows(throughput=4,error=.2,p95=400,canary=False)
    assert evaluate(bad,require_state_canary=True)['status']=='FAIL'
    missing=dict(TECH);missing['fault_absent']=None
    assert evaluate_recovery(baseline=BASE,post_windows=windows(),technical=missing,
        repair_completed_at=20,evaluated_at=52)['status']=='UNKNOWN'
    sparse=windows()
    sparse=[WorkloadWindow(**{**w.__dict__,'samples':3}) for w in sparse]
    assert evaluate(sparse)['status']=='UNKNOWN'


def test_invalid_types_nonfinite_baseline_and_missing_canary_are_unknown():
    invalid={'fault_absent':0,'resource_restored':'false','ready_endpoints':[]}
    assert evaluate_recovery(baseline=BASE,post_windows=windows(),technical=invalid,
        repair_completed_at=20,evaluated_at=52)['status']=='UNKNOWN'
    badbase=WorkloadWindow(samples=0,throughput_rps=float('inf'),error_rate=0,p95_ms=100,
        window_id='baseline',source_id='workload-a',start_time=0,end_time=10)
    assert evaluate_recovery(baseline=badbase,post_windows=windows(),technical=TECH,
        repair_completed_at=20,evaluated_at=52)['status']=='UNKNOWN'
    no_canary=windows(canary=None)
    assert evaluate(no_canary,require_state_canary=True)['status']=='UNKNOWN'
    import pytest
    with pytest.raises(ValueError,match='required_windows'):evaluate_recovery(
            baseline=BASE,post_windows=[],technical=TECH,required_windows=0,
            repair_completed_at=20,evaluated_at=52)


def test_repeated_cross_source_pre_repair_and_stale_windows_are_unknown():
    duplicate=windows();duplicate[1]=WorkloadWindow(
        **{**duplicate[1].__dict__,'window_id':'post-0'})
    assert evaluate(duplicate)['status']=='UNKNOWN'
    other_source=windows();other_source[0]=WorkloadWindow(
        **{**other_source[0].__dict__,'source_id':'workload-b'})
    assert evaluate(other_source)['status']=='UNKNOWN'
    pre_repair=windows();pre_repair[0]=WorkloadWindow(
        **{**pre_repair[0].__dict__,'start_time':19})
    assert evaluate(pre_repair)['status']=='UNKNOWN'
    assert evaluate_recovery(baseline=BASE,post_windows=windows(),technical=TECH,
        repair_completed_at=20,evaluated_at=1000,max_window_age_seconds=120)['status']=='UNKNOWN'


def test_old_passing_streak_cannot_hide_a_fresh_failure():
    old=windows()
    fresh_failure=WorkloadWindow(samples=30,throughput_rps=1,error_rate=.5,p95_ms=500,
        state_canary_ok=False,window_id='fresh-failure',source_id='workload-a',
        start_time=990,end_time=999)
    result=evaluate_recovery(baseline=BASE,post_windows=old+[fresh_failure],technical=TECH,
        repair_completed_at=20,evaluated_at=1000,max_window_age_seconds=120,
        require_state_canary=True)
    assert result['status']!='PASS' and not result['success']
    assert result['trailing_consecutive_passing_windows']==0
