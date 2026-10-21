"""Actual registered-probe accounting across initial, adaptive and mandatory reads."""
from dataclasses import asdict
import copy
import math
import time
from .runtime import ObservationBudget


class ProbeBudgetExceeded(RuntimeError):
    pass


class ProbeAccountant:
    def __init__(self, budget, clock=time.perf_counter, record=None):
        self.budget = budget
        if any(not math.isfinite(v) for v in asdict(budget).values()):
            raise ValueError('probe budgets must be finite')
        self.clock = clock
        self.record = record
        self.started = clock()
        self.closed = None
        self.authorization_checks = []
        self.decision_boundary=None
        self.phase = 'initial'
        self.events = []
        self.calls = 0
        self.cost = 0.
        self.seconds = 0.
        self.exceeded = False

    def elapsed(self):
        return (self.closed if self.closed is not None else self.clock())-self.started

    def permit_write(self):
        elapsed=self.elapsed()
        approved=not self.exceeded and elapsed<=self.budget.max_seconds
        self.authorization_checks.append({'elapsed_seconds':elapsed,'approved':approved})
        return approved

    def close(self):
        if self.closed is None:self.closed=self.clock()
        self.phase=None

    def mark_decision_boundary(self):
        self.decision_boundary={'calls':self.calls,'cost':self.cost,'probe_seconds':self.seconds,
                                'elapsed_seconds':self.elapsed(),'recorded_at':time.time()}
        return self.decision_boundary

    def invoke(self, probe, function, *args):
        if self.phase is None:
            return function(*args)
        event = {'sequence':len(self.events), 'phase': self.phase, 'probe_id': probe.id, 'declared_cost': probe.cost,
                 'declared_timeout': probe.timeout, 'started': False}
        self.events.append(event)
        if (self.calls + 1 > self.budget.max_calls or self.cost + probe.cost > self.budget.max_cost
                or self.elapsed() + probe.timeout > self.budget.max_seconds):
            event['status'] = 'not_started_budget_exhausted'
            if self.record:self.record(copy.deepcopy(event))
            raise ProbeBudgetExceeded('registered probe does not fit remaining total budget')
        self.calls += 1
        self.cost += probe.cost
        started = self.clock()
        event.update(started=True, status='started', started_at=time.time())
        if self.record:self.record(copy.deepcopy(event))
        try:
            result = function(*args)
        except Exception as exc:
            event.update(status='failed', error=type(exc).__name__)
            raise
        else:
            event['status'] = 'observed'
        finally:
            elapsed = self.clock() - started
            self.seconds += elapsed
            event['seconds'] = elapsed
            if elapsed > probe.timeout or self.elapsed() > self.budget.max_seconds:
                self.exceeded = True
                event['budget_exceeded'] = True
            if self.record:self.record(copy.deepcopy(event))
        if self.exceeded:
            raise ProbeBudgetExceeded('actual probe runtime exceeded declared total/per-probe budget')
        return result

    def remaining(self, limit, reserve):
        return ObservationBudget(
            max_calls=max(0, min(limit.max_calls, self.budget.max_calls-self.calls-reserve['calls'])),
            max_cost=max(0., min(limit.max_cost, self.budget.max_cost-self.cost-reserve['cost'])),
            max_seconds=max(0., min(limit.max_seconds, self.budget.max_seconds-self.elapsed()-reserve['seconds'])))

    def report(self):
        return {'schema': 'total_registered_probe_budget_v1', 'limits': asdict(self.budget),
                'calls': self.calls, 'cost': self.cost, 'probe_seconds': self.seconds,
                'elapsed_through_close_seconds':self.elapsed(),
                'pre_write_authorization_checks':list(self.authorization_checks),
                'decision_boundary':self.decision_boundary,
                'actual_runtime_exceeded': self.exceeded, 'events': list(self.events),
                'scope': 'Initial + adaptive + mandatory registered probes; attempted failures charged. One monotonic deadline from before base collection through pre-write authorization includes API reads and inference. Registered cost omits passive API I/O. Close may follow controller convergence; pre-write checks show the applicable deadline. Baseline/verification/cleanup separately timed.'}


def refresh_reserve(provider, graph, plans):
    budgets=[]
    for plan in plans:
        probes=[p for action in plan.actions for p in provider.refresh_probes(graph,action)]
        budgets.append({'calls': len(probes), 'cost': sum(p.cost for p in probes), 'seconds': sum(p.timeout for p in probes)})
    return {key: max((b[key] for b in budgets), default=0) for key in ('calls','cost','seconds')}
