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

    # -- consensus ------------------------------------------------------------
    # Blocks this node mined itself, versus what the network sent it. The gap
    # between ``blocks_new`` and ``blocks_stale`` is the fork rate: a block that
    # arrives valid but loses the race is wasted work, and wasted work is the
    # cost that bounds a Proof-of-Work chain's throughput.
    blocks_mined: int = 0
    blocks_new: int = 0
    blocks_duplicate: int = 0
    blocks_orphan: int = 0
    blocks_invalid: int = 0
    #: Valid blocks that landed on a branch we are not following.
    blocks_stale: int = 0
    reorgs: int = 0
    #: Total blocks undone across all reorganisations; divided by ``reorgs`` it
    #: gives the average depth, which is what a confirmation rule has to beat.
    reorg_depth: int = 0
    hashes_tried: int = 0

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

    def record_block(self, status: str) -> None:
        """Count one block by what the consensus layer decided about it."""
        counter = {
            "extended": "blocks_new",
            "reorg": "blocks_new",
            "side": "blocks_stale",
            "duplicate": "blocks_duplicate",
            "orphan": "blocks_orphan",
            "invalid": "blocks_invalid",
        }.get(status)
        if counter is not None:
            setattr(self, counter, getattr(self, counter) + 1)

    def record_reorg(self, depth: int) -> None:
        self.reorgs += 1
        self.reorg_depth += depth

    def record_mined(self, hashes: int = 0) -> None:
        self.blocks_mined += 1
        self.hashes_tried = max(self.hashes_tried, hashes)

    # -- derived --------------------------------------------------------------

    @property
    def tx_received_total(self) -> int:
        return self.tx_new + self.tx_duplicate

    @property
    def redundancy_ratio(self) -> float:
        """Fraction of received transactions that were duplicates."""
        total = self.tx_received_total
        return self.tx_duplicate / total if total else 0.0

    @property
    def stale_block_rate(self) -> float:
        """Share of valid blocks that lost their race and earned nothing.

        The Proof-of-Work analogue of the redundancy ratio: both measure work
        the network paid for and did not use.
        """
        total = self.blocks_new + self.blocks_stale
        return self.blocks_stale / total if total else 0.0

    def to_dict(self) -> dict:
        return {
            "packets_sent": self.packets_sent,
            "packets_received": self.packets_received,
            "tx_new": self.tx_new,
            "tx_duplicate": self.tx_duplicate,
            "redundancy_ratio": round(self.redundancy_ratio, 4),
            "blocks_mined": self.blocks_mined,
            "blocks_new": self.blocks_new,
            "blocks_stale": self.blocks_stale,
            "blocks_duplicate": self.blocks_duplicate,
            "blocks_orphan": self.blocks_orphan,
            "blocks_invalid": self.blocks_invalid,
            "reorgs": self.reorgs,
            "reorg_depth": self.reorg_depth,
            "stale_block_rate": round(self.stale_block_rate, 4),
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
    mined = sum(m.blocks_mined for m in all_metrics)
    stale = sum(m.blocks_stale for m in all_metrics)
    accepted = sum(m.blocks_new for m in all_metrics)
    reorgs = sum(m.reorgs for m in all_metrics)
    reorg_depth = sum(m.reorg_depth for m in all_metrics)
    return {
        "nodes": n,
        "avg_packets_sent": round(total_sent / n, 2),
        "avg_packets_received": round(total_recv / n, 2),
        "total_packets_sent": total_sent,
        "total_duplicates": total_dup,
        "avg_duplicates_per_node": round(total_dup / n, 2),
        "redundancy_ratio": round(total_dup / total_tx_recv, 4) if total_tx_recv else 0.0,
        "blocks_mined": mined,
        "reorgs": reorgs,
        "avg_reorg_depth": round(reorg_depth / reorgs, 2) if reorgs else 0.0,
        # Averaged over nodes rather than summed: every node sees (almost) every
        # block, so the sum would just scale with the fleet size.
        "stale_block_rate": round(stale / (accepted + stale), 4) if accepted + stale else 0.0,
    }
