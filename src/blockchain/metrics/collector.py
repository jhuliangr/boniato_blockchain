"""Per-node metric counters and cross-node aggregation.

One :class:`Metrics` instance lives on each node's community and is bumped as
messages flow. After a run the harness collects them all and calls
:func:`aggregate` to produce the comparison table for Push / Pull / Hybrid.

Metrics tracked:

- ``packets_sent`` / ``packets_received`` raw wire traffic (all message types).
- ``tx_new`` / ``tx_duplicate`` accepted transactions that were fresh vs.
  already known (duplicates are the classic gossip-overhead signal).
- **Extra metric (group's choice): redundancy ratio** = duplicates / total tx
  messages received. 0 means every delivery was useful; higher means more
  wasted bandwidth. It complements raw duplicate counts by normalising them.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class Metrics:
    """Mutable counters for a single node."""

    packets_sent: int = 0
    packets_received: int = 0
    tx_new: int = 0
    tx_duplicate: int = 0
    # Fine-grained breakdown by message name, useful when debugging a strategy.
    sent_by_type: Counter = field(default_factory=Counter)
    received_by_type: Counter = field(default_factory=Counter)

    # -- recording ------------------------------------------------------------

    def record_sent(self, message_type: str, count: int = 1) -> None:
        self.packets_sent += count
        self.sent_by_type[message_type] += count

    def record_received(self, message_type: str) -> None:
        self.packets_received += 1
        self.received_by_type[message_type] += 1

    def record_transaction(self, *, is_new: bool) -> None:
        if is_new:
            self.tx_new += 1
        else:
            self.tx_duplicate += 1

    # -- derived --------------------------------------------------------------

    @property
    def tx_received_total(self) -> int:
        return self.tx_new + self.tx_duplicate

    @property
    def redundancy_ratio(self) -> float:
        """Fraction of received transactions that were duplicates."""
        total = self.tx_received_total
        return self.tx_duplicate / total if total else 0.0

    def to_dict(self) -> dict:
        return {
            "packets_sent": self.packets_sent,
            "packets_received": self.packets_received,
            "tx_new": self.tx_new,
            "tx_duplicate": self.tx_duplicate,
            "redundancy_ratio": round(self.redundancy_ratio, 4),
            "sent_by_type": dict(self.sent_by_type),
            "received_by_type": dict(self.received_by_type),
        }


def aggregate(all_metrics: list[Metrics]) -> dict:
    """Summarise a whole network's metrics into the comparison numbers."""
    n = len(all_metrics)
    if n == 0:
        return {}
    total_sent = sum(m.packets_sent for m in all_metrics)
    total_recv = sum(m.packets_received for m in all_metrics)
    total_dup = sum(m.tx_duplicate for m in all_metrics)
    total_tx_recv = sum(m.tx_received_total for m in all_metrics)
    return {
        "nodes": n,
        "avg_packets_sent": round(total_sent / n, 2),
        "avg_packets_received": round(total_recv / n, 2),
        "total_packets_sent": total_sent,
        "total_duplicates": total_dup,
        "avg_duplicates_per_node": round(total_dup / n, 2),
        "redundancy_ratio": round(total_dup / total_tx_recv, 4) if total_tx_recv else 0.0,
    }
