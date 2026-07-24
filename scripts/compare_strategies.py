#!/usr/bin/env python3
"""Benchmark Push vs. Pull vs. Hybrid under identical conditions.

Runs each strategy on a freshly built network with the same seed, then prints
the comparison table the brief asks for: average packets sent, duplicates and
the group's extra metric (propagation coverage / redundancy ratio).

Example::

    python scripts/compare_strategies.py --nodes 60 --max-peers 8 --duration 20
"""

from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse
import asyncio

from blockchain.metrics import aggregate
from blockchain.simulation import Simulation, SimulationConfig

STRATEGIES = {
    "push": {},
    "pull": {},
    "hybrid": {"fanout": 3, "poll_targets": 1},
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    # Defaults chosen for lossless, single-process measurement. Beyond ~20
    # nodes a single event loop cannot drain the UDP sockets fast enough during
    # push floods, so packets drop (a real distributed-systems effect); scale up
    # with eyes open. Topology experiments (run_network.py) scale to 100 fine.
    p.add_argument("--nodes", type=int, default=15)
    p.add_argument("--max-peers", type=int, default=6)
    p.add_argument("--initial-connections", type=int, default=3)
    p.add_argument("--duration", type=float, default=12.0)
    p.add_argument(
        "--settle",
        type=float,
        default=8.0,
        help="quiescence time after stopping production, before measuring",
    )
    p.add_argument("--tx-interval", type=float, default=3.0)
    p.add_argument("--tick-interval", type=float, default=1.0)
    p.add_argument("--seed", type=int, default=1234)
    return p.parse_args()


async def run_one(args: argparse.Namespace, strategy: str, kwargs: dict) -> dict:
    config = SimulationConfig(
        num_nodes=args.nodes,
        strategy=strategy,
        strategy_kwargs=kwargs,
        max_peers=args.max_peers,
        initial_connections=args.initial_connections,
        tx_interval=args.tx_interval,
        tick_interval=args.tick_interval,
        seed=args.seed,
    )
    sim = Simulation(config)
    await sim.start()
    await asyncio.sleep(args.duration)
    # Stop producing and let the network settle so we measure converged state.
    sim.pause_production()
    await asyncio.sleep(args.settle)
    result = {
        "strategy": strategy,
        "coverage": sim.coverage(),
        "traffic": aggregate(sim.collect_metrics()),
    }
    await sim.stop()
    return result


def print_table(results: list[dict]) -> None:
    header = f"{'strategy':<8} {'avg_sent':>10} {'avg_recv':>10} {'duplicates':>11} {'redundancy':>11} {'coverage':>9} {'full%':>7}"
    print("\n" + header)
    print("-" * len(header))
    for r in results:
        t, c = r["traffic"], r["coverage"]
        print(
            f"{r['strategy']:<8} {t['avg_packets_sent']:>10.1f} {t['avg_packets_received']:>10.1f} "
            f"{t['total_duplicates']:>11d} {t['redundancy_ratio']:>11.3f} "
            f"{c['avg_coverage']:>9.3f} {c['fully_propagated_pct']:>6.1f}%"
        )


async def main() -> None:
    args = parse_args()
    print(
        f"Comparing strategies: {args.nodes} nodes, max_peers={args.max_peers}, {args.duration}s each"
    )
    results = []
    for strategy, kwargs in STRATEGIES.items():
        print(f"  running {strategy}…")
        results.append(await run_one(args, strategy, kwargs))
    print_table(results)


if __name__ == "__main__":
    asyncio.run(main())
