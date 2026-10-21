"""One budget/reservation/selector path for native execution and timed replay."""
from .probe_budget import refresh_reserve
from .runtime import refine_and_decide,apply_profile_status


def budgeted_decision(graph,plans,policy,predictor,provider,observe,*,budget,account=None,strategy='none',seed=42,fixed_order=None,candidate_ranker=None,adaptive_probes=None):
    reserve=None
    if account is not None:
        account.phase='adaptive'
        reserve=refresh_reserve(provider,graph,plans)
        budget=account.remaining(budget,reserve)
    result=refine_and_decide(graph,plans,policy,predictor,provider.probes if adaptive_probes is None else adaptive_probes,observe,
        budget=budget,strategy=strategy,seed=seed,fixed_order=fixed_order,candidate_ranker=candidate_ranker)
    if account is not None:
        result['mandatory_refresh_reserve']=reserve
        result['total_probe_budget']=account.report()
        if account.exceeded:apply_profile_status(result,'total_probe_runtime_exceeded')
    return result
