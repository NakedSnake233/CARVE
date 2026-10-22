"""Three-valued technical + business post-repair oracle for controlled experiments."""
from __future__ import annotations

from dataclasses import dataclass,asdict
from typing import Any,Dict,List
import math


@dataclass(frozen=True)
class WorkloadWindow:
    samples:int
    throughput_rps:float
    error_rate:float
    p95_ms:float
    state_canary_ok:bool|None=None
    window_id:str=''
    source_id:str=''
    start_time:float|None=None
    end_time:float|None=None


def evaluate_recovery(*,baseline:WorkloadWindow,post_windows:List[WorkloadWindow],
                      technical:Dict[str,bool|None],required_windows:int=3,
                      min_samples:int=20,error_margin:float=.01,
                      p95_multiplier:float=1.2,throughput_fraction:float=.8,
                      require_state_canary:bool=False,
                      repair_completed_at:float|None=None,
                      evaluated_at:float|None=None,
                      max_window_age_seconds:float=120.0)->Dict[str,Any]:
    """PASS only when all measurable technical checks and consecutive windows pass.

    Missing checks/samples are UNKNOWN. UNKNOWN is never counted as success.
    """
    if required_windows<1:raise ValueError('required_windows must be >=1')
    if min_samples<1:raise ValueError('min_samples must be >=1')
    if (not isinstance(error_margin,(int,float)) or isinstance(error_margin,bool)
            or not math.isfinite(error_margin) or error_margin<0):
        raise ValueError('error_margin must be finite and non-negative')
    if (not isinstance(p95_multiplier,(int,float)) or isinstance(p95_multiplier,bool)
            or not math.isfinite(p95_multiplier) or p95_multiplier<=0):
        raise ValueError('p95_multiplier must be finite and positive')
    if (not isinstance(throughput_fraction,(int,float)) or isinstance(throughput_fraction,bool)
            or not math.isfinite(throughput_fraction) or not 0<=throughput_fraction<=1):
        raise ValueError('throughput_fraction must be within [0,1]')
    if (not isinstance(max_window_age_seconds,(int,float)) or isinstance(max_window_age_seconds,bool)
            or not math.isfinite(max_window_age_seconds) or max_window_age_seconds<=0):
        raise ValueError('max_window_age_seconds must be finite and positive')

    def finite_number(value):
        return (isinstance(value,(int,float)) and not isinstance(value,bool)
                and math.isfinite(value))

    repair_time_valid=finite_number(repair_completed_at)
    evaluation_time_valid=finite_number(evaluated_at)
    required=('fault_absent','resource_restored','ready_endpoints')
    invalid_technical=[key for key in required if technical.get(key) not in (True,False,None) or type(technical.get(key)) not in (bool,type(None))]
    missing=[key for key in required if technical.get(key) is None or key in invalid_technical]
    failed=[key for key in required if technical.get(key) is False and type(technical.get(key)) is bool]
    baseline_identity_valid=(bool(baseline.window_id) and bool(baseline.source_id)
                             and finite_number(baseline.start_time)
                             and finite_number(baseline.end_time)
                             and baseline.start_time<baseline.end_time
                             and repair_time_valid
                             and baseline.end_time<=repair_completed_at)
    baseline_valid=(isinstance(baseline.samples,int) and not isinstance(baseline.samples,bool)
                    and baseline.samples>=min_samples
                    and all(finite_number(v) for v in (baseline.throughput_rps,baseline.error_rate,baseline.p95_ms))
                    and baseline.throughput_rps>0 and 0<=baseline.error_rate<=1 and baseline.p95_ms>0)
    details=[];seen_ids=set();previous_end=None
    for index,window in enumerate(post_windows):
        measurable=(isinstance(window.samples,int) and not isinstance(window.samples,bool)
                    and window.samples>=min_samples
                    and all(finite_number(v) for v in (window.throughput_rps,window.error_rate,window.p95_ms))
                    and window.throughput_rps>=0 and 0<=window.error_rate<=1 and window.p95_ms>=0)
        state_type_valid=type(window.state_canary_ok) in (bool,type(None))
        state_unknown=(not state_type_valid or
                       (require_state_canary and window.state_canary_ok is None))
        state_ok=(window.state_canary_ok is True) if require_state_canary else (
            state_type_valid and window.state_canary_ok is not False)
        identity_valid=(bool(window.window_id) and window.window_id not in seen_ids
                        and window.source_id==baseline.source_id
                        and finite_number(window.start_time)
                        and finite_number(window.end_time)
                        and window.start_time<window.end_time
                        and repair_time_valid and window.start_time>=repair_completed_at
                        and (previous_end is None or window.start_time>=previous_end))
        if window.window_id:seen_ids.add(window.window_id)
        if finite_number(window.end_time):previous_end=window.end_time
        passed=(measurable and identity_valid and state_ok and window.error_rate<=baseline.error_rate+error_margin
                and window.p95_ms<=baseline.p95_ms*p95_multiplier
                and window.throughput_rps>=baseline.throughput_rps*throughput_fraction)
        details.append({'index':index,'window':asdict(window),'measurable':measurable,
                        'identity_valid':identity_valid,'state_ok':state_ok,
                        'state_unknown':state_unknown,'passed':passed})
    consecutive=0;maximum=0
    for row in details:
        consecutive=consecutive+1 if row['passed'] else 0;maximum=max(maximum,consecutive)
    trailing_consecutive=consecutive
    last_end=post_windows[-1].end_time if post_windows else None
    qualifying_windows=(post_windows[-required_windows:]
                        if len(post_windows)>=required_windows else [])
    freshness_valid=(evaluation_time_valid
                     and len(qualifying_windows)==required_windows
                     and all(finite_number(window.end_time)
                             and window.end_time<=evaluated_at
                             and evaluated_at-window.end_time<=max_window_age_seconds
                             for window in qualifying_windows))
    if (not baseline_valid or not baseline_identity_valid or not freshness_valid
            or missing or len(post_windows)<required_windows
            or any(not row['measurable'] or not row['identity_valid'] or row['state_unknown'] for row in details)):
        status='UNKNOWN'
    elif failed or trailing_consecutive<required_windows:status='FAIL'
    else:status='PASS'
    return {'status':status,'success':status=='PASS','technical':technical,
            'missing_technical_checks':missing,'invalid_technical_checks':invalid_technical,'failed_technical_checks':failed,
            'baseline_measurable':baseline_valid,'baseline_identity_valid':baseline_identity_valid,
            'repair_completed_at':repair_completed_at,'evaluated_at':evaluated_at,
            'measurement_freshness_valid':freshness_valid,
            'required_consecutive_windows':required_windows,
            'max_consecutive_passing_windows':maximum,
            'trailing_consecutive_passing_windows':trailing_consecutive,
            'thresholds':{'min_samples':min_samples,'error_rate_max':baseline.error_rate+error_margin,
                          'p95_ms_max':baseline.p95_ms*p95_multiplier,
                          'throughput_rps_min':baseline.throughput_rps*throughput_fraction,
                          'require_state_canary':require_state_canary,
                          'max_window_age_seconds':max_window_age_seconds},'windows':details}
