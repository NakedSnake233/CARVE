"""Bounded, read-only RESP observations through a registered Redis Service."""
from __future__ import annotations

import socket
import time

from .redis_transaction import command

READS = {('INFO', 'clients'), ('CONFIG', 'GET', 'maxclients'), ('CLIENT', 'LIST')}
MAX_REPLY_BYTES = 1_048_576


def read_command(address, parts, *, deadline):
    parts = tuple(parts)
    if parts not in READS:
        raise ValueError('unregistered Redis observation command')

    def remaining():
        seconds = deadline - time.perf_counter()
        if seconds <= 0:
            raise TimeoutError('Redis observation deadline exceeded')
        return seconds

    with socket.create_connection(address, timeout=remaining()) as conn:
        buffer = bytearray()
        received = 0

        def receive(amount):
            nonlocal received
            conn.settimeout(remaining())
            data = conn.recv(min(amount, MAX_REPLY_BYTES - received + 1))
            if not data:
                raise OSError('Redis closed before a complete observation')
            remaining()
            received += len(data)
            if received > MAX_REPLY_BYTES:
                raise ValueError('Redis observation exceeds response limit')
            return data

        def line():
            while True:
                end = buffer.find(b'\r\n')
                if end >= 0:
                    if end > 510:
                        raise ValueError('oversized Redis protocol line')
                    value = bytes(buffer[:end])
                    del buffer[:end + 2]
                    remaining()
                    if value.startswith(b'-'):
                        raise ValueError('Redis observation error: ' + value[1:201].decode('utf-8', 'replace'))
                    return value
                if len(buffer) >= 512:
                    raise ValueError('incomplete Redis protocol line')
                buffer.extend(receive(512 - len(buffer)))

        def bulk():
            header = line()
            if not header.startswith(b'$'):
                raise ValueError('expected Redis bulk observation')
            length = int(header[1:])
            if not 0 <= length <= MAX_REPLY_BYTES - 2:
                raise ValueError('invalid Redis bulk observation length')
            while len(buffer) < length + 2:
                buffer.extend(receive(min(65536, length + 2 - len(buffer))))
            data = bytes(buffer[:length])
            if buffer[length:length + 2] != b'\r\n':
                raise ValueError('invalid Redis bulk terminator')
            del buffer[:length + 2]
            remaining()
            return data.decode('utf-8')

        conn.settimeout(remaining())
        conn.sendall(command(*parts))
        if parts == ('CONFIG', 'GET', 'maxclients'):
            if line() != b'*2':
                raise ValueError('expected one Redis configuration key/value pair')
            result = [bulk(), bulk()]
            if result[0] != 'maxclients':
                raise ValueError('Redis configuration key mismatch')
        else:
            result = bulk()
        remaining()
        return result
