"""Monotonic stage timing; records completed and exceptional stages alike."""
from contextlib import contextmanager
from time import perf_counter


@contextmanager
def measure(timings, stage):
    start = perf_counter()
    try:
        yield
    finally:
        timings[stage] = timings.get(stage, 0.0) + perf_counter() - start
