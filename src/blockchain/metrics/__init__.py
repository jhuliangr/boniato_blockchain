"""Instrumentation: what the network did, and how well it did it.

- :mod:`~blockchain.metrics.collector` -- per-node counters. Packets, duplicates
  and the group's redundancy ratio for the gossip comparison (phase 3), plus the
  block counters the consensus layer bumps: accepted, stale, orphaned, rejected,
  and reorganisations with their depth.
- :mod:`~blockchain.metrics.benchmark` -- throughput and latency of a live
  chain, with the warm-up and end phases excluded, as
  ``scripts/benchmark.py`` reports them.

Both are passive: they are handed events, they never go looking for them, and
nothing in ``core``, ``consensus`` or ``execution`` imports either. A node that
is not being measured pays nothing.
"""

from blockchain.metrics.benchmark import (
    ChainBenchmark,
    PropagationBenchmark,
    TransactionRecord,
    describe,
)
from blockchain.metrics.collector import Metrics, aggregate

__all__ = [
    "Metrics",
    "aggregate",
    "ChainBenchmark",
    "PropagationBenchmark",
    "TransactionRecord",
    "describe",
]
