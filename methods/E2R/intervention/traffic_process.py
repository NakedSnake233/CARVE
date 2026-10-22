"""Bounded native traffic worker with explicit GC and RPC timing boundaries."""
from __future__ import annotations

import gc
import multiprocessing
from multiprocessing.reduction import ForkingPickler
import resource
import time
import traceback


def _serve(connection, specs, enable_cyclic_gc, dispatch_delay_floor_seconds):
    from .campaign import collector
    from .traffic import ConcurrentTraffic
    traffic=None
    gc_beginnings={};gc_events=[];rpc_events=[]
    prior_gc=gc.isenabled()
    def gc_event(phase, info):
        generation=info['generation']
        if phase=='start':
            gc_beginnings[generation]=(time.time(),time.perf_counter())
        else:
            start,clock=gc_beginnings.pop(generation,(time.time(),time.perf_counter()))
            elapsed=time.perf_counter()-clock
            if elapsed>=.001:
                gc_events.append({'generation':generation,'start_time':start,
                                  'duration_seconds':elapsed,'collected':info['collected']})
                del gc_events[:-64]
    def diagnostics():
        base=traffic.diagnostics() if traffic else {'drained':True}
        return {**base,'gc_pauses':list(gc_events),'gc_during_workload':enable_cyclic_gc,
                'gc_counts':gc.get_count(),'gc_stats':gc.get_stats(),
                'rpc_events':list(rpc_events),'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    def reply(packet, method, started, wall_clock, cpu_clock):
        built=time.perf_counter();cpu_built=time.thread_time()
        encoded=ForkingPickler.dumps(packet)
        serialized=time.perf_counter();cpu_serialized=time.thread_time()
        connection.send_bytes(encoded)
        rpc_events.append({'method':method,'start_time':started,
            'build_wall_seconds':built-wall_clock,'build_thread_cpu_seconds':cpu_built-cpu_clock,
            'pickle_wall_seconds':serialized-built,'pickle_thread_cpu_seconds':cpu_serialized-cpu_built,
            'send_wall_seconds':time.perf_counter()-serialized,'send_thread_cpu_seconds':time.thread_time()-cpu_serialized})
        del rpc_events[:-64]
    gc.callbacks.append(gc_event)
    try:
        if any(s.get('protocol')=='native_cart_grpc' for s in specs.values()):
            import grpc
            from .cart_transaction import messages
            messages()
        workloads={name:collector(spec) for name,spec in specs.items()}
        # Explicitly collect before pacing. Normal cyclic GC remains the default;
        # disabling it is supported only for bounded diagnostic comparisons.
        gc.collect()
        if enable_cyclic_gc:gc.enable()
        else:gc.disable()
        traffic=ConcurrentTraffic(workloads,dispatch_delay_floor_seconds=dispatch_delay_floor_seconds)
        traffic.start()
        connection.send({'ok':True,'value':traffic.origin_wall})
        while True:
            method,args=connection.recv()
            started=time.time();clock=time.perf_counter();cpu=time.thread_time()
            stopping=False
            try:
                if method=='take':value=traffic.take(*args)
                elif method=='history':value=traffic.history()
                elif method=='diagnostics':value=diagnostics()
                elif method=='stop':
                    traffic.stop(*args)
                    # All request workers are joined before explicit collection.
                    gc.collect()
                    value={'history':traffic.history(),'diagnostics':diagnostics()}
                    stopping=True
                else:raise ValueError('unknown traffic request')
                packet={'ok':True,'value':value}
            except Exception as exc:
                packet={'ok':False,'type':type(exc).__name__,'message':str(exc),
                        'traceback':traceback.format_exc(),'diagnostics':diagnostics()}
            reply(packet,method,started,clock,cpu)
            if stopping:return
    except EOFError:
        pass
    except Exception as exc:
        try:
            connection.send({'ok':False,'type':type(exc).__name__,'message':str(exc),
                             'traceback':traceback.format_exc(),'diagnostics':diagnostics()})
        except (OSError,EOFError):pass
    finally:
        try:
            if traffic:traffic.stop()
        finally:
            gc.callbacks.remove(gc_event)
            if prior_gc:gc.enable()
            else:gc.disable()
            connection.close()


class ProcessTraffic:
    def __init__(self, specs, observed_streams=None, enable_cyclic_gc=True, dispatch_delay_floor_seconds=.25):
        self.specs = specs
        self.enable_cyclic_gc = enable_cyclic_gc
        self.dispatch_delay_floor_seconds = dispatch_delay_floor_seconds
        self.observed_streams = set(specs if observed_streams is None else observed_streams)
        if not self.observed_streams <= set(specs):
            raise ValueError('observed streams must have an offered workload')
        self.process = None
        self._history, self._diagnostics = [], {}
        self.stopped = False
        self.ipc_failed = False

    def _receive(self, timeout):
        if not self.connection.poll(timeout):
            self.ipc_failed = True
            self._diagnostics['ipc_error'] = 'reply_timeout'
            raise TimeoutError('traffic process did not reply; retain ownership until drained')
        try:
            reply = self.connection.recv()
        except (EOFError,OSError):
            self.ipc_failed = True
            self._diagnostics['ipc_error'] = 'connection_closed'
            raise RuntimeError('traffic process connection closed')
        if not reply['ok']:
            self._diagnostics = {**reply.get('diagnostics',{}),'remote_traceback':reply.get('traceback')}
            kind = TimeoutError if reply.get('type') == 'TimeoutError' else RuntimeError
            raise kind(reply['message'])
        return reply['value']

    def _call(self, method, *args, timeout=30):
        if self.ipc_failed:
            raise RuntimeError('traffic IPC failed; refuse further requests on this channel')
        try:
            self.connection.send((method,args))
        except (OSError,EOFError):
            self.ipc_failed = True
            self._diagnostics['ipc_error'] = 'send_failed'
            raise RuntimeError('traffic process connection closed')
        return self._receive(timeout)

    def start(self):
        if self.process is not None:
            raise RuntimeError('traffic process already started')
        ctx = multiprocessing.get_context('spawn')
        self.connection, child = ctx.Pipe()
        self.process = ctx.Process(target=_serve,args=(child,self.specs,self.enable_cyclic_gc,self.dispatch_delay_floor_seconds),name='e2r-traffic',daemon=True)
        self.process.start()
        child.close()
        self.origin_wall = self._receive(60)
        return self

    def take(self, after, count=1, timeout=180):
        cycles=self._call('take',after,count,timeout,timeout=timeout+30)
        return [{n:value for n,value in cycle.items() if n in self.observed_streams} for cycle in cycles]

    def history(self):
        if self.stopped or self.ipc_failed or not self.process.is_alive():
            return self._history
        self._history = self._call('history')
        return self._history

    def diagnostics(self):
        if not (self.stopped or self.ipc_failed or not self.process.is_alive()):
            self._diagnostics = {**self._diagnostics,**self._call('diagnostics')}
        return {**self._diagnostics,'drained':not self.process.is_alive()}

    def stop(self, timeout=60):
        if self.stopped:
            return
        value = self._call('stop',timeout,timeout=timeout+30)
        self._history = value['history']
        self._diagnostics = {**self._diagnostics,**value['diagnostics']}
        self.process.join(5)
        if self.process.is_alive():
            raise TimeoutError('traffic process still alive; next reset must not start')
        self.stopped = True
        self.connection.close()
