"""Pluggable gossip strategies: Push, Pull and Hybrid.

The brief asks for three interchangeable propagation strategies whose overhead
we then compare. We model them with the **Strategy pattern**: the community
owns transport and state; a :class:`GossipStrategy` decides *when* to send
*what*. Swapping strategies changes propagation behaviour without touching the
community or the domain.

The strategy talks back to the community through a tiny, documented interface
(a "gossip context"), so the two stay loosely coupled:

- ``ctx.peers()``                       -> current neighbours (list of Peer)
- ``ctx.send_transaction(peer, tx)``    -> push one transaction to a peer
- ``ctx.request_inventory(peer)``       -> ask a peer what it holds (pull)
- ``ctx.random_peers(k)``               -> up to ``k`` random neighbours

Termination / loop-freedom is guaranteed by the mempool: a transaction only
triggers :meth:`on_transaction_accepted` the first time a node sees it, so each
node forwards any given transaction at most once.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class GossipStrategy(ABC):
    """Base class for propagation strategies."""

    #: Human-readable name used in metrics/reports.
    name: str = "base"

    @abstractmethod
    def on_transaction_accepted(self, ctx, transaction, source) -> None:
        """Called once, when a *newly* accepted transaction enters the mempool.

        ``source`` is the peer we received it from, or ``None`` if this node
        created it locally.
        """

    def on_tick(self, ctx) -> None:
        """Called periodically. Default: do nothing (Push overrides nothing)."""


class PushGossip(GossipStrategy):
    """Eager push: on a new transaction, forward it to every neighbour.

    Fast and simple, but every node re-broadcasts, so dense networks see many
    duplicates exactly the overhead we want to measure.
    """

    name = "push"

    def __init__(self, fanout: int | None = None) -> None:
        # ``None`` => flood to all neighbours; an int caps the fanout.
        self.fanout = fanout

    def on_transaction_accepted(self, ctx, transaction, source) -> None:
        targets = ctx.peers() if self.fanout is None else ctx.random_peers(self.fanout)
        for peer in targets:
            if peer == source:
                continue  # never echo back to the sender
            ctx.send_transaction(peer, transaction)


class PullGossip(GossipStrategy):
    """Lazy pull: never forward; instead periodically ask a neighbour for its
    inventory and fetch whatever is missing.

    Minimal wasted bandwidth (few duplicates) at the cost of higher latency —
    propagation speed is bounded by the polling interval.
    """

    name = "pull"

    def __init__(self, poll_targets: int = 1) -> None:
        self.poll_targets = poll_targets

    def on_transaction_accepted(self, ctx, transaction, source) -> None:
        # Pull is passive on new transactions.
        return

    def on_tick(self, ctx) -> None:
        for peer in ctx.random_peers(self.poll_targets):
            ctx.request_inventory(peer)


class HybridGossip(GossipStrategy):
    """Push to a small random subset for speed, and pull periodically to close
    any gaps the limited push missed.

    Aims for the best of both: near-push latency with far fewer duplicates than
    a full flood, and pull as a safety net for completeness.
    """

    name = "hybrid"

    def __init__(self, fanout: int = 3, poll_targets: int = 1) -> None:
        self.fanout = fanout
        self.poll_targets = poll_targets

    def on_transaction_accepted(self, ctx, transaction, source) -> None:
        for peer in ctx.random_peers(self.fanout):
            if peer == source:
                continue
            ctx.send_transaction(peer, transaction)

    def on_tick(self, ctx) -> None:
        for peer in ctx.random_peers(self.poll_targets):
            ctx.request_inventory(peer)


def make_strategy(name: str, **kwargs) -> GossipStrategy:
    """Factory: build a strategy by name (``push`` / ``pull`` / ``hybrid``)."""
    strategies = {
        "push": PushGossip,
        "pull": PullGossip,
        "hybrid": HybridGossip,
    }
    try:
        return strategies[name](**kwargs)
    except KeyError:
        raise ValueError(f"unknown gossip strategy: {name!r} (choose from {sorted(strategies)})")
