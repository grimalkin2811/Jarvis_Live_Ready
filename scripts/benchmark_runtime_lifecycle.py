"""Offline microbenchmark: synthetic Core lifecycle, registry and context.

Uses the repository's test doubles: no audio hardware, network, or database.
Run `python -m scripts.benchmark_runtime_lifecycle` from the repository root
with its test dependencies installed.
"""
from __future__ import annotations

import asyncio
import gc
import json
import platform
import time
import tracemalloc

from src.core.context import ContextRequest
from src.core.runtime import CoreRuntime
from src.runtime_status import RuntimeStatusBoard
from tests.test_core_runtime import config, factory_set


async def main():
    cycles = 20
    creation, startup, shutdown, retained = [], [], [], []
    tracemalloc.start()
    for _ in range(cycles):
        gc.collect()
        start = time.perf_counter()
        core = CoreRuntime(config(), factories=factory_set([]), status=RuntimeStatusBoard())
        creation.append(time.perf_counter() - start)
        start = time.perf_counter()
        task = asyncio.create_task(core.run())
        while core.provider is None:
            await asyncio.sleep(0)
        await core.provider.ready.wait()
        startup.append(time.perf_counter() - start)
        if len(creation) == 1:
            registry = core.services
            start = time.perf_counter()
            for _ in range(10_000):
                registry.require('memory')
            lookup = time.perf_counter() - start
            engine = registry.require('context')
            request = ContextRequest(query='test', conversation=False, runtime=False)
            start = time.perf_counter()
            for _ in range(500):
                engine.build(request)
            context = time.perf_counter() - start
            del engine, registry, request
        start = time.perf_counter()
        core.request_stop()
        await task
        shutdown.append(time.perf_counter() - start)
        del core, task
        gc.collect()
        retained.append(tracemalloc.get_traced_memory()[0])
    current, peak = tracemalloc.get_traced_memory()
    print(json.dumps({
        'platform': platform.platform(), 'python': platform.python_version(), 'cycles': cycles,
        'creation_ms_mean': sum(creation) * 1000 / cycles,
        'startup_ms_mean': sum(startup) * 1000 / cycles,
        'shutdown_ms_mean': sum(shutdown) * 1000 / cycles,
        'lookup_10000_seconds': lookup, 'context_500_seconds': context,
        'python_bytes_after_cycle_1': retained[0], 'python_bytes_after_cycle_20': retained[-1],
        'python_bytes_current': current, 'python_bytes_peak': peak,
        'limitations': 'synthetic test doubles; tracemalloc excludes native allocations and RSS',
    }, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
