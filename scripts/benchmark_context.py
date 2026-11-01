"""Offline Context Engine microbenchmark (not an end-to-end UI benchmark)."""
from __future__ import annotations

import argparse
import json
import platform
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.conversation import ConversationContext  # noqa: E402
from src.core.context import ContextEngine, ContextRequest  # noqa: E402
from src.core.services import ServiceRegistry  # noqa: E402


def run(iterations: int) -> dict:
    results = []
    for count in (5, 30, 200):
        conversation = ConversationContext(max_turns=200, max_tokens=200000, conversation_id='benchmark')
        for n in range(count):
            conversation.add_user_message(f'Python projet numéro {n} : ' + 'texte artificiel ' * 10)
        registry = ServiceRegistry()
        registry.register('conversation', conversation)
        engine = ContextEngine(registry)
        request = ContextRequest(query='Python projet', runtime=False, budget_tokens=2048)
        engine.build(request)
        tracemalloc.start()
        wall, cpu = time.perf_counter(), time.process_time()
        for _ in range(iterations):
            engine.build(request)
        wall, cpu = time.perf_counter() - wall, time.process_time() - cpu
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        results.append({'messages': count, 'iterations': iterations, 'wall_seconds': round(wall, 4),
                        'cpu_seconds': round(cpu, 4), 'peak_python_bytes': peak})
    return {'platform': platform.platform(), 'python': platform.python_version(), 'results': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--iterations', type=int, default=500)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 100000:
        parser.error('iterations must be between 1 and 100000')
    print(json.dumps(run(args.iterations), indent=2, ensure_ascii=False))
