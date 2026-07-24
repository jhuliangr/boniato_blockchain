#!/usr/bin/env python3
"""Run a network of peers with one gossip strategy and report the results.

Examples
--------
Dense network, push gossip, 100 peers for 30s::

    python scripts/run_network.py --nodes 100 --strategy push --max-peers 20 --duration 30

Sparse network, pull gossip, export the topology graph::

    python scripts/run_network.py --nodes 100 --strategy pull --max-peers 2 \
        --topology-out topo.json --dot-out topo.dot
"""

from __future__ import annotations

import _bootstrap  # noqa: F401  (adds src/ to sys.path)

import argparse
import asyncio
import json

from blockchain.metrics import aggregate
from blockchain.simulation import Simulation, SimulationConfig


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--nodes", type=int, default=100, help="number of peers (default 100)"
    )
    p.add_argument("--strategy", choices=["push", "pull", "hybrid"], default="push")
    p.add_argument(
        "--max-peers",
        type=int,
        default=10,
        help="neighbour cap (small=sparse, large=dense)",
    )
    p.add_argument(
        "--initial-connections", type=int, default=3, help="random seed links per node"
    )
    p.add_argument("--duration", type=float, default=30.0, help="run time in seconds")
    p.add_argument(
        "--tx-interval",
        type=float,
        default=5.0,
        help="seconds between dummy transactions",
    )
    p.add_argument(
        "--tick-interval", type=float, default=2.0, help="seconds between gossip ticks"
    )
    p.add_argument("--seed", type=int, default=1234)
    p.add_argument(
        "--fanout",
        type=int,
        default=None,
        help="push/hybrid fanout (default: flood for push)",
    )
    p.add_argument(
        "--topology-out", type=str, default=None, help="write topology JSON here"
    )
    p.add_argument("--dot-out", type=str, default=None, help="write Graphviz DOT here")
    return p.parse_args()


def build_strategy_kwargs(args: argparse.Namespace) -> dict:
    if args.strategy == "push":
        return {"fanout": args.fanout}
    if args.strategy == "hybrid":
        return {"fanout": args.fanout or 3}
    return {}


async def main() -> None:
    args = parse_args()
    config = SimulationConfig(
        num_nodes=args.nodes,
        strategy=args.strategy,
        strategy_kwargs=build_strategy_kwargs(args),
        max_peers=args.max_peers,
        initial_connections=args.initial_connections,
        tx_interval=args.tx_interval,
        tick_interval=args.tick_interval,
        seed=args.seed,
    )

    sim = Simulation(config)
    print(f"Starting {args.nodes} peers ({config.preset_label()})…")
    await sim.start()
    print(f"Running for {args.duration}s…")
    await asyncio.sleep(args.duration)

    topology = sim.build_topology()
    report = {
        "config": config.preset_label(),
        "topology": topology.stats(),
        "coverage": sim.coverage(),
        "traffic": aggregate(sim.collect_metrics()),
    }
    await sim.stop()

    print("\n=== RESULT ===")
    print(json.dumps(report, indent=2))

    if args.topology_out:
        with open(args.topology_out, "w") as f:
            f.write(topology.to_json())
        print(f"\nTopology JSON -> {args.topology_out}")
    if args.dot_out:
        with open(args.dot_out, "w") as f:
            f.write(topology.to_dot())
        print(
            f"Topology DOT  -> {args.dot_out}  (render: dot -Tpng {args.dot_out} -o topo.png)"
        )


if __name__ == "__main__":
    asyncio.run(main())
