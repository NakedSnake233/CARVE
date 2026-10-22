"""Bounded SET/GET load against the study's real Redis server (not an effect simulator)."""
from __future__ import annotations
import socket
import time
import urllib.parse
import uuid


def command(*parts):
    encoded=[p if isinstance(p,bytes) else str(p).encode() for p in parts]
    return b'*'+str(len(encoded)).encode()+b'\r\n'+b''.join(b'$'+str(len(p)).encode()+b'\r\n'+p+b'\r\n' for p in encoded)


def redis_transaction(spec,detail=None,*,deadline=None):
    detail={} if detail is None else detail
    started=time.perf_counter()
    if deadline is None:deadline=time.monotonic()+float(spec.get('timeout',3))
    uri=urllib.parse.urlparse(spec['url'])
    if uri.scheme!='redis' or uri.username or uri.password:raise ValueError('registered private Redis URL required')
    size=spec.get('payload_bytes',262144)
    if type(size) is not int or not 64<=size<=262144:raise ValueError('background cache payload must be 64..262144 bytes')
    def remaining():
        budget=deadline-time.monotonic()
        if budget<=0:raise TimeoutError('Redis transaction deadline exceeded')
        return budget
    try:
        detail['redis_stage']='connect'
        with socket.create_connection((uri.hostname,uri.port or 6379),timeout=remaining()) as conn:
            buffer=bytearray()
            def receive(amount):
                conn.settimeout(remaining())
                data=conn.recv(amount)
                if not data:raise OSError('Redis connection closed before complete response')
                remaining()
                return data
            def line():
                while True:
                    end=buffer.find(b'\r\n')
                    if end>=0:
                        if end+2>512:raise ValueError('oversized Redis protocol line')
                        value=bytes(buffer[:end+2]);del buffer[:end+2]
                        remaining();return value
                    if len(buffer)>=512:raise ValueError('incomplete Redis protocol line')
                    buffer.extend(receive(512-len(buffer)))
            def exact(amount):
                while len(buffer)<amount:
                    buffer.extend(receive(min(65536,amount-len(buffer))))
                value=bytes(buffer[:amount]);del buffer[:amount]
                remaining();return value
            detail['redis_stage']='send'
            conn.settimeout(remaining())
            if spec.get('read_only'):
                conn.sendall(command('PING'));detail['redis_stage']='ping_reply';ok=line()==b'+PONG\r\n'
            else:
                value=uuid.uuid4().hex.encode()+b'x'*(size-32)
                key='e2r-native-background-'+str(spec['key_slot'])
                conn.settimeout(remaining())
                conn.sendall(command('SET',key,value)+command('GET',key))
                detail['redis_stage']='set_reply';first=line()
                if first!=b'+OK\r\n':
                    ok=False;detail['failure']='redis_response_error'
                    detail['redis_reply']=first.decode('utf-8','replace')[:200]
                else:
                    detail['redis_stage']='get_length';second=line()
                    if second!=b'$'+str(size).encode()+b'\r\n':
                        ok=False;detail['failure']='redis_response_error'
                        detail['redis_reply']=second.decode('utf-8','replace')[:200]
                    else:
                        detail['redis_stage']='get_value'
                        ok=exact(size)==value and exact(2)==b'\r\n'
                        if not ok:detail['failure']='stored_bytes_mismatch'
            remaining()
    except (OSError,TimeoutError,ValueError) as exc:
        ok=False;detail['failure']=type(exc).__name__
    return ok,(time.perf_counter()-started)*1000
