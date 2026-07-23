"""Instrumentation for the gossip experiments (Phase 3).

Collects the numbers the brief asks to compare across Push / Pull / Hybrid:
average packets sent, duplicates received, plus an extra group-chosen metric
(propagation coverage / redundancy ratio).
"""

from blockchain.metrics.collector import Metrics, aggregate

__all__ = ["Metrics", "aggregate"]
