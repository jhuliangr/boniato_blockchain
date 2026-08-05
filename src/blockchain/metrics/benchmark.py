"""Measuring a running chain: throughput, latency, and what they cost.

The counters in :mod:`blockchain.metrics.collector` say what the network *did*.
This module says how well it did it, in the two numbers a blockchain is actually
judged on:

**Throughput** -- confirmed transactions per second, and blocks per second.
Counted over a measurement window, never over the whole run.

**Latency** -- how long a transaction takes to become real. Split in two,
because they are different quantities with different causes:

- *inclusion* latency: submitted until it appears in a block. Bounded by how
  often blocks are found, and by how many transactions fit in one.
- *confirmation* latency at depth ``k``: submitted until ``k-1`` further blocks
  are stacked on top of the one holding it. This is the number a user waits for,
  and it is the one that couples latency to security -- a larger ``k`` is a
  smaller chance of a reorg undoing the transaction, bought with time.

Two methodological rules, both from the lecture and both easy to get wrong:

**Warm-up and end phases do not count.** Peers spend the first seconds finding
each other, and the last blocks of a run are still in flight when it stops. A
transaction submitted in the final second would be recorded as never confirmed,
which would drag the average down for a reason that has nothing to do with the
system. Only transactions submitted inside the window are scored, and the run
must keep going after the window closes so they have a chance to confirm.

**Latency is measured from one vantage point.** "Confirmed" is not a global
fact: it is a thing an observer node believes. All timings here are taken at a
single observer, so a number means "how long until *this* node was convinced",
which is what a user connected to that node would experience.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field


@dataclass
class TransactionRecord:
    """One transaction's journey from submission to confirmation."""

    tx_hash: bytes
    submitted_at: float
    #: In the measurement window, as opposed to warm-up or tail.
    counted: bool = True
    included_at: float | None = None
    included_height: int | None = None

    @property
    def inclusion_latency(self) -> float | None:
        if self.included_at is None:
            return None
        return self.included_at - self.submitted_at


@dataclass
class ChainBenchmark:
    """Follows transactions and blocks on one observer node.

    Feed it :meth:`on_submit` when a client sends a transaction and
    :meth:`on_block` for every block the observer connects. It works out the
    rest, including confirmation depth, from the heights it has seen.
    """

    #: Transactions submitted, by hash.
    transactions: dict[bytes, TransactionRecord] = field(default_factory=dict)
    #: When the observer first reached each height. Confirmation depth is read
    #: off this: a transaction in a block at height ``h`` is ``k`` deep the
    #: moment the observer reaches ``h + k - 1``.
    height_times: dict[int, float] = field(default_factory=dict)
    #: Blocks connected inside the window, for the block-rate figure.
    blocks_counted: int = 0
    #: Set while the measurement window is open.
    counting: bool = False
    window_started_at: float | None = None
    window_ended_at: float | None = None

    # -- recording ------------------------------------------------------------

    def start_window(self, at: float) -> None:
        self.counting = True
        self.window_started_at = at

    def end_window(self, at: float) -> None:
        self.counting = False
        self.window_ended_at = at

    def on_submit(self, tx_hash: bytes, at: float) -> None:
        if tx_hash in self.transactions:
            return  # a resubmission is the same transaction, not a second one
        self.transactions[tx_hash] = TransactionRecord(
            tx_hash=tx_hash, submitted_at=at, counted=self.counting
        )

    def on_block(self, block, height: int, at: float) -> None:
        """Record a block the observer just connected.

        Reorganisations are handled by *not* handling them specially: a
        transaction's inclusion time is the first time the observer saw it in a
        block, which is when a user watching that node would have been told it
        was in. If a reorg later moves it, the honest thing to report is the
        confirmation latency at a depth deep enough that reorgs do not reach --
        which is exactly what ``k`` is for.
        """
        self.height_times.setdefault(height, at)
        if self.counting:
            self.blocks_counted += 1
        for tx in block.transactions:
            record = self.transactions.get(tx.tx_hash)
            if record is not None and record.included_at is None:
                record.included_at = at
                record.included_height = height

    # -- results --------------------------------------------------------------

    @property
    def window_seconds(self) -> float:
        if self.window_started_at is None or self.window_ended_at is None:
            return 0.0
        return self.window_ended_at - self.window_started_at

    def _counted(self) -> list[TransactionRecord]:
        return [t for t in self.transactions.values() if t.counted]

    def confirmed(self, k: int = 1) -> list[TransactionRecord]:
        """Counted transactions that reached depth ``k`` before the run ended."""
        top = max(self.height_times, default=0)
        return [
            t
            for t in self._counted()
            if t.included_height is not None and t.included_height + k - 1 <= top
        ]

    def confirmation_latencies(self, k: int = 1) -> list[float]:
        """Submission-to-``k``-deep, in seconds, for every transaction that got there."""
        latencies = []
        for record in self.confirmed(k):
            confirmed_at = self.height_times.get(record.included_height + k - 1)
            if confirmed_at is not None:
                latencies.append(confirmed_at - record.submitted_at)
        return latencies

    def inclusion_latencies(self) -> list[float]:
        return [
            t.inclusion_latency for t in self._counted() if t.inclusion_latency is not None
        ]

    def throughput(self) -> dict:
        """Confirmed transactions and blocks per second over the window."""
        seconds = self.window_seconds
        if seconds <= 0:
            return {"tx_per_second": 0.0, "blocks_per_second": 0.0}
        return {
            "tx_per_second": round(len(self.confirmed(1)) / seconds, 3),
            "blocks_per_second": round(self.blocks_counted / seconds, 3),
        }

    def summary(self, depths: tuple[int, ...] = (1, 3, 6)) -> dict:
        """Everything worth reporting, in one dictionary."""
        submitted = self._counted()
        included = [t for t in submitted if t.included_at is not None]
        result = {
            "window_seconds": round(self.window_seconds, 2),
            "submitted": len(submitted),
            "included": len(included),
            #: Submitted but never mined by the time the run ended. A non-zero
            #: figure here is the honest signal that the chain was offered more
            #: work than it could take.
            "unconfirmed": len(submitted) - len(included),
            **self.throughput(),
            "inclusion_latency": describe(self.inclusion_latencies()),
        }
        for k in depths:
            result[f"confirm_latency_k{k}"] = describe(self.confirmation_latencies(k))
        return result


def describe(values: list[float]) -> dict:
    """Median and tail of a sample. The mean alone hides exactly what matters.

    A chain's latency distribution is skewed by construction -- Proof-of-Work
    block intervals are exponential -- so the median says what a user usually
    waits and p95 says what they occasionally wait. Reporting only the mean
    would land between the two and describe neither.
    """
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "median": round(statistics.median(ordered), 3),
        "mean": round(statistics.fmean(ordered), 3),
        "p95": round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 3),
        "max": round(ordered[-1], 3),
    }


@dataclass
class PropagationBenchmark:
    """How long a block takes to reach the rest of the network.

    The ``delta`` in the throughput bound from the lecture: a Proof-of-Work
    chain is safe while blocks are found more slowly than they spread, so this
    is the quantity that decides how hard blocks are allowed to be.
    """

    #: block hash -> the earliest time any node connected it.
    first_seen: dict[bytes, float] = field(default_factory=dict)
    #: One delay per (block, node) pair, measured from that earliest time.
    delays: list[float] = field(default_factory=list)

    def on_block(self, block_hash: bytes, at: float) -> None:
        origin = self.first_seen.get(block_hash)
        if origin is None:
            self.first_seen[block_hash] = at
            return  # the first sighting is the origin, and has no delay of its own
        self.delays.append(at - origin)

    def summary(self) -> dict:
        return {"blocks": len(self.first_seen), "delay_seconds": describe(self.delays)}
