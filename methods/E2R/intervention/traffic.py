"""Independently paced concurrent native transactions, with fixed dispatch windows."""
from __future__ import annotations

import concurrent.futures
import math
import queue
import threading
import time
from dataclasses import asdict
from rca_bench.recovery_oracle import WorkloadWindow


class ConcurrentTraffic:
    """Dispatch continues across window boundaries and slow competitor responses.

    Each stream owns its worker pool and paced scheduler. Windows group requests
    by scheduled arrival; completion tails and client scheduling delay are saved.
    Taking a window never starts or pauses traffic. A saturated client is an
    experiment error rather than a silently reduced offered load.
    """
    def __init__(self, workloads, *, max_cycles=4096, dispatch_delay_floor_seconds=.25):
        if (type(dispatch_delay_floor_seconds) not in (int,float)
                or not math.isfinite(dispatch_delay_floor_seconds) or dispatch_delay_floor_seconds<=0):
            raise ValueError('dispatch delay floor must be a finite positive number')
        self.dispatch_delay_floor_seconds=float(dispatch_delay_floor_seconds)
        self.workloads, self.max_cycles = workloads, max_cycles
        self.cycles, self.pending = [], {}
        self.invalid_windows = []
        self.warmup = {}
        self.stop_event = threading.Event()
        self.ready = threading.Event()
        self.barrier = threading.Barrier(len(workloads)+1)
        self.condition = threading.Condition()
        self.error = self.thread = None

    def start(self):
        if self.thread is not None:
            raise RuntimeError("traffic session already started")
        durations = {w.window_duration_seconds for w in self.workloads.values()}
        if len(durations) != 1 or None in durations or next(iter(durations)) <= 0:
            raise ValueError("continuous streams require equal positive window durations")
        self.thread = threading.Thread(target=self._run, daemon=True, name="e2r-continuous-transactions")
        self.thread.start()
        if not self.ready.wait(30):
            self.stop_event.set()
            self.barrier.abort()
            raise TimeoutError("traffic initialization did not complete")
        if self.error:
            raise RuntimeError("traffic initialization failed") from self.error
        return self

    def _fail(self, error):
        with self.condition:
            if self.error is None:
                self.error = error
            self.stop_event.set()
            self.condition.notify_all()

    def _run(self):
        threads = [threading.Thread(target=self._stream, args=(name, workload), daemon=True)
                   for name, workload in self.workloads.items()]
        for thread in threads:
            thread.start()
        try:
            self.barrier.wait(timeout=25)
            # All pools/stream threads exist before the first scheduled arrival.
            self.origin_wall, self.origin_mono = time.time()+.1, time.monotonic()+.1
        except Exception as exc:
            self._fail(exc)
        finally:
            self.ready.set()
        for thread in threads:
            thread.join()
        with self.condition:
            self.condition.notify_all()

    def _stream(self, name, workload):
        batches = queue.Queue()
        writer = threading.Thread(target=self._finish, args=(name, workload, batches), daemon=True)
        writer.start()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workload.workers) as pool:
                # Restoration can change short successful requests into long
                # timeouts. Provision that concurrency before measured traffic,
                # rather than creating OS threads on the paced dispatch path.
                spec=getattr(workload,'spec',{})
                timeout=spec.get('timeout',getattr(workload,'timeout',5))
                budget=sum(s.get('timeout',timeout) for s in spec.get('steps',[{}]))
                count=min(workload.workers,max(1,math.ceil(workload.offered_rps*budget)+2))
                if spec.get('protocol')=='redis_kv':
                    count=min(workload.workers,max(count,workload.redis_connection_budget))
                initialized=threading.Barrier(count+1)
                warm=[pool.submit(initialized.wait,20) for _ in range(count)]
                initialized.wait(timeout=20)
                for future in warm:future.result()
                self.warmup[name]={'workers_started':count,'transaction_timeout_budget_seconds':budget}
                self.barrier.wait(timeout=25)
                self.ready.wait()
                duration, rate, index = workload.window_duration_seconds, workload.offered_rps, 0
                while not self.stop_event.is_set():
                    futures = []
                    first = math.ceil(index*duration*rate-1e-9)
                    last = math.ceil((index+1)*duration*rate-1e-9)
                    for request_index in range(first, last):
                        scheduled = self.origin_mono + request_index/rate
                        if self.stop_event.wait(max(0, scheduled-time.monotonic())):
                            break
                        submitted = time.monotonic()
                        def request(due=scheduled, submitted=submitted):
                            began = time.monotonic()
                            if hasattr(workload,'request_with_details'):
                                ok,latency,detail=workload.request_with_details()
                            else:
                                ok,latency=workload._request();detail={}
                            return ok, latency, began-due, time.time(),detail, submitted-due, began-submitted
                        futures.append(pool.submit(request))
                    if len(futures) != last-first:
                        break
                    end = self.origin_mono+(index+1)*duration
                    if self.stop_event.wait(max(0, end-time.monotonic())):
                        break
                    canary = pool.submit(workload.state_canary) if workload.state_canary else None
                    batches.put((index, futures, canary))
                    index += 1
        except Exception as exc:
            self.barrier.abort()
            self._fail(exc)
        finally:
            batches.put(None)
            writer.join()

    def _finish(self, name, workload, batches):
        try:
            while True:
                batch = batches.get()
                if batch is None:
                    return
                index, futures, canary = batch
                samples = [f.result() for f in futures]
                duration = workload.window_duration_seconds
                latencies = sorted(float(s[1])+1000*max(0,s[2]) for s in samples)
                success = sum(s[0] is True for s in samples)
                delay = max(s[2] for s in samples)
                legacy_tolerance = max(.25, 1/workload.offered_rps)
                tolerance = max(self.dispatch_delay_floor_seconds, 1/workload.offered_rps)
                start = self.origin_wall+index*duration
                nominal_end = start+duration
                end = max(nominal_end, max(s[3] for s in samples))
                state = canary.result() if canary else None
                window = WorkloadWindow(len(samples), success/duration, (len(samples)-success)/len(samples),
                    latencies[max(0, math.ceil(.95*len(latencies))-1)], state,
                    f"traffic-{index}-{name}", workload.source_id, start, nominal_end, time.time())
                raw = {"attempts": len(samples), "successes": success, "offered_rps": workload.offered_rps,
                       "target_window_seconds": duration, "scheduled_window_end": nominal_end,
                       "completion_tail_seconds": end-nominal_end, "max_dispatch_delay_seconds": delay,
                       "dispatch_tolerance_seconds": tolerance,
                       "legacy_dispatch_tolerance_seconds": legacy_tolerance,
                       "legacy_dispatch_tolerance_exceeded": delay > legacy_tolerance,
                       "requests_exceeding_legacy_tolerance": sum(s[2]>legacy_tolerance for s in samples),
                       "latencies_ms": latencies, "throughput_denominator": "fixed arrival window",
                       "transaction_results": [{"ok":s[0],"latency_ms":s[1],"dispatch_delay_seconds":s[2],"end_time":s[3],
                                                "diagnostics":s[4], "scheduler_delay_seconds":s[5],
                                                "executor_delay_seconds":s[6]} for s in samples]}
                reason = None
                if any(s[4].get('failure','').startswith('client_') for s in samples):
                    reason = "workload client connection budget exhausted; not a valid backend measurement"
                elif delay > tolerance:
                    reason = "workload client saturated; scheduled arrival delayed beyond declared tolerance"
                if reason:
                    with self.condition:
                        self.invalid_windows.append({"stream":name,"index":index,"reason":reason,
                                                     "window":asdict(window),"raw":raw})
                    raise RuntimeError(f"{reason}: stream={name}, window={index}, delay={delay:.6f}s")
                with self.condition:
                    cycle = self.pending.setdefault(index, {})
                    cycle[name] = (window, raw)
                    if len(cycle) == len(self.workloads):
                        self.cycles.append(self.pending.pop(index))
                        if len(self.cycles)>self.max_cycles:
                            self.cycles.pop(0)
                    self.condition.notify_all()
        except Exception as exc:
            self._fail(exc)

    def take(self, after, count=1, timeout=180):
        deadline = time.monotonic()+timeout
        with self.condition:
            while True:
                if self.error is not None:
                    raise RuntimeError("continuous workload failed") from self.error
                eligible = [c for c in self.cycles if all(w.start_time >= after for w,_ in c.values())]
                if len(eligible)>=count:
                    return eligible[:count]
                if self.thread is None or not self.thread.is_alive():
                    raise RuntimeError("workload stopped before enough windows were measured")
                remaining = deadline-time.monotonic()
                if remaining<=0:
                    raise TimeoutError("workload windows not available within deadline")
                self.condition.wait(min(remaining,2))

    def history(self):
        with self.condition:
            return [{n:{"window":asdict(w),"raw":r} for n,(w,r) in c.items()} for c in self.cycles]

    def diagnostics(self):
        with self.condition:
            return {"error":str(self.error) if self.error else None,"invalid_windows":list(self.invalid_windows),
                    "drained":self.thread is not None and not self.thread.is_alive(),"warmup":dict(self.warmup),
                    "dispatch_delay_floor_seconds":self.dispatch_delay_floor_seconds}

    def stop(self, timeout=60):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout)
            if self.thread.is_alive():
                raise TimeoutError("workload workers did not stop; next reset must not start")
        for workload in self.workloads.values():
            if hasattr(workload,'close_transports'):
                workload.close_transports()
        # Measurement validity is exposed by take()/diagnostics(). Successful
        # drain does not make that measurement valid, nor is it a shutdown error.
