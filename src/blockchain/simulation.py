"""In-process simulation harness for many peers (Phase 3).

Spins up ``N`` :class:`BlockchainCommunity` peers on the loopback interface,
seeds an initial random topology, and lets them gossip. Afterwards it can:

- collect every node's :class:`~blockchain.metrics.Metrics`,
- capture the live :class:`~blockchain.topology.Topology`,
- measure **propagation coverage** the group's extra metric.

Connectivity is controlled two ways, matching the brief's sparse-vs-dense
experiment:

- ``max_peers``         hard cap on neighbours per node (small = sparse).
- ``initial_connections``random seed links before the RandomWalk spreads.

No external bootstrap servers are used; peers discover each other purely from
the seed links via IPv8's introduction mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass

from ipv8.configuration import ConfigBuilder, Strategy, WalkerDefinition
from ipv8_service import IPv8

from blockchain.crypto import Identity
from blockchain.metrics import Metrics
from blockchain.network import BlockchainCommunity, NodeConfig, make_strategy


@dataclass
class SimulationConfig:
    """Parameters of a single experiment run."""

    num_nodes: int = 100
    strategy: str = "push"
    strategy_kwargs: dict | None = None
    max_peers: int = 8
    initial_connections: int = 2
    tx_interval: float = 5.0
    tick_interval: float = 2.0
    seed: int = 1234

    def preset_label(self) -> str:
        return f"{self.strategy}/maxpeers={self.max_peers}"


class Simulation:
    """Owns the lifecycle of a fleet of peers for one experiment."""

    def __init__(self, config: SimulationConfig) -> None:
        self.config = config
        self._instances: list[IPv8] = []
        self.communities: list[BlockchainCommunity] = []

    # -- lifecycle ------------------------------------------------------------

    async def start(self) -> None:
        cfg = self.config
        for i in range(cfg.num_nodes):
            identity = Identity.generate()
            # Each node gets its own strategy instance and metrics collector.
            strategy = make_strategy(cfg.strategy, **(cfg.strategy_kwargs or {}))
            node = NodeConfig(
                identity=identity,
                strategy=strategy,
                metrics=Metrics(),
                name=f"node-{i:03d}",
                tx_interval=cfg.tx_interval,
                tick_interval=cfg.tick_interval,
                rng_seed=cfg.seed + i,
            )
            instance = self._build_instance(node)
            await instance.start()
            self._instances.append(instance)
            self.communities.append(instance.get_overlay(BlockchainCommunity))

        self._seed_topology()

    async def stop(self) -> None:
        import asyncio

        # First silence every node's periodic tasks so they stop generating
        # traffic; otherwise the still-running nodes flood the event loop and
        # starve the shutdown of the ones being torn down.
        for community in self.communities:
            community.cancel_all_pending_tasks()
        # Then tear all instances down concurrently.
        await asyncio.gather(*(instance.stop() for instance in self._instances))
        self._instances.clear()
        self.communities.clear()

    def _build_instance(self, node: NodeConfig) -> IPv8:
        cfg = self.config
        builder = ConfigBuilder().clear_keys().clear_overlays()
        builder.set_log_level("ERROR")  # keep 100-node output readable
        builder.add_ephemeral_key("my_peer")  # transport key (separate from tx identity)
        # Bind to 0.0.0.0 so the address IPv8 advertises (its estimated LAN
        # address) is actually reachable during peer exchange binding to
        # 127.0.0.1 would make the advertised LAN address undeliverable.
        builder.set_address("0.0.0.0")
        builder.set_port(0)  # OS-assigned free port
        builder.add_overlay(
            "BlockchainCommunity",
            "my_peer",
            [WalkerDefinition(Strategy.RandomWalk, cfg.max_peers, {"timeout": 3.0})],
            [],  # no external bootstrappers: purely local simulation
            {"max_peers": cfg.max_peers, "node": node},
            [],
        )
        return IPv8(builder.finalize(), extra_communities={"BlockchainCommunity": BlockchainCommunity})

    def _seed_topology(self) -> None:
        """Introduce each node to a few random others to bootstrap discovery."""
        import random

        rng = random.Random(self.config.seed)
        addresses = [c.my_estimated_lan for c in self.communities]
        n = len(self.communities)
        for i, community in enumerate(self.communities):
            others = [j for j in range(n) if j != i]
            rng.shuffle(others)
            for j in others[: self.config.initial_connections]:
                community.walk_to(addresses[j])

    def pause_production(self) -> None:
        """Halt transaction production on every node (gossip keeps running)."""
        for community in self.communities:
            community.pause_production()

    # -- observation ----------------------------------------------------------

    def collect_metrics(self) -> list[Metrics]:
        return [c.metrics for c in self.communities]

    def build_topology(self):
        """Snapshot the current connectivity as a :class:`Topology`."""
        from blockchain.topology import Topology

        adjacency = {
            self._node_id(c): [self._peer_id(p) for p in c.get_peers()] for c in self.communities
        }
        return Topology.from_adjacency(adjacency)

    def coverage(self) -> dict:
        """Propagation coverage: how widely transactions have spread.

        For every transaction seen anywhere, we compute the fraction of nodes
        that hold it. ``avg_coverage == 1.0`` means every node has every
        transaction (full dissemination).
        """
        n = len(self.communities)
        seen_counts: dict[bytes, int] = {}
        for community in self.communities:
            for tx_hash in community.mempool.hashes():
                seen_counts[tx_hash] = seen_counts.get(tx_hash, 0) + 1

        if not seen_counts:
            return {"unique_transactions": 0, "avg_coverage": 0.0, "fully_propagated": 0}

        coverages = [count / n for count in seen_counts.values()]
        fully = sum(1 for count in seen_counts.values() if count == n)
        return {
            "unique_transactions": len(seen_counts),
            "avg_coverage": round(sum(coverages) / len(coverages), 4),
            "fully_propagated": fully,
            "fully_propagated_pct": round(100 * fully / len(seen_counts), 2),
        }

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _node_id(community: BlockchainCommunity) -> str:
        return community.my_peer.mid.hex()[:10]

    @staticmethod
    def _peer_id(peer) -> str:
        return peer.mid.hex()[:10]
