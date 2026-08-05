#!/usr/bin/env python3
"""Benchmark the chain: throughput, latency, and how both scale.

Method, following the course's benchmarking lecture:

- **Poisson load.** Clients submit real game actions at exponentially
  distributed intervals, to randomly chosen peers. Constant-rate submission
  would smooth away exactly the queueing behaviour worth measuring.
- **Warm-up, window, drain.** Peers need seconds to find each other, and a
  transaction submitted in the last second cannot possibly confirm. Only the
  middle window is scored; the run keeps going afterwards so its transactions
  get their chance.
- **A baseline to scale against.** The smallest configuration is the baseline
  and every other one is reported relative to it, so the table says whether
  adding nodes bought anything.

Two experiments:

``scaling``
    More nodes, **the same total mining power**. Full replication says this
    should not make the chain faster -- every node still validates every
    transaction -- while the traffic each node handles grows. The table is there
    to show that, and to put a number on the cost.

``difficulty``
    The throughput bound from the lecture, ``lambda * delta``: blocks per second
    times how long a block takes to spread. Making blocks easier raises the
    block rate until they are found faster than they propagate, at which point
    miners build on stale heads and the work is thrown away. The stale-block
    rate is where that shows up.

``load``
    **Capacity, as opposed to demand.** The other two experiments offer a load
    the chain can absorb, so their transactions-per-second figure measures what
    was asked for, not what could be delivered. This one raises the offered rate
    until confirmed throughput stops following it. Where the two part company is
    the capacity; what happens to latency past that point is queueing.

Examples
--------
Everything (~8 minutes)::

    python scripts/benchmark.py

One experiment, shorter windows (~2 minutes)::

    python scripts/benchmark.py --only scaling --quick

Write the numbers out for the report::

    python scripts/benchmark.py --json docs/benchmarks.json
"""

from __future__ import annotations

import _bootstrap  # noqa: F401  (adds src/ to sys.path)

import argparse
import asyncio
import json
import random
import time

from blockchain.crypto import Identity
from blockchain.execution import BuyLand, Claim, Harvest, Plant, signed
from blockchain.metrics import ChainBenchmark, PropagationBenchmark, aggregate
from blockchain.simulation import Simulation, SimulationConfig

#: Total network hash rate held constant across the scaling experiment, so the
#: only thing changing is how many nodes share it.
TOTAL_HASHES_PER_SECOND = 24_000
MINE_INTERVAL = 0.1


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--only", choices=["scaling", "difficulty", "load"], default=None)
    p.add_argument("--warmup", type=float, default=8.0, help="seconds before counting")
    p.add_argument("--window", type=float, default=30.0, help="measurement window")
    p.add_argument(
        "--drain", type=float, default=12.0, help="seconds to let the tail confirm"
    )
    p.add_argument(
        "--rate", type=float, default=4.0, help="offered load, transactions per second"
    )
    p.add_argument("--nodes", type=int, nargs="+", default=[2, 4, 8, 16])
    p.add_argument("--difficulties", type=int, nargs="+", default=[12, 14, 16, 18])
    p.add_argument(
        "--base-difficulty", type=int, default=16, help="used by --only scaling"
    )
    p.add_argument(
        "--base-nodes", type=int, default=8, help="used by --only difficulty"
    )
    p.add_argument("--rates", type=float, nargs="+", default=[2, 5, 12, 25, 50])
    p.add_argument("--load-nodes", type=int, default=4, help="used by --only load")
    p.add_argument(
        "--load-difficulty", type=int, default=14, help="used by --only load"
    )
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--quick", action="store_true", help="short windows, fewer points")
    p.add_argument("--json", type=str, default=None, help="write raw results here")
    return p.parse_args()


# -- load ----------------------------------------------------------------------


class Clients:
    """Wallets submitting plausible actions against whatever state they can see."""

    def __init__(self, count: int, seed: int) -> None:
        self.identities = [Identity.generate() for _ in range(count)]
        self._rng = random.Random(seed)
        self._claimed: set[bytes] = set()

    def next_action(self, state):
        identity = self._rng.choice(self.identities)
        key = identity.public_key
        if key not in self._claimed:
            self._claimed.add(key)
            return identity, Claim()
        owned = state.lands_of(key)
        ready = [p for p in owned if p.is_planted]
        fallow = [p for p in owned if not p.is_planted]
        if ready:
            return identity, Harvest(land_id=self._rng.choice(ready).land_id)
        if fallow:
            return identity, Plant(land_id=self._rng.choice(fallow).land_id)
        if state.balance_of(key) > state.next_land_price:
            return identity, BuyLand()
        return identity, Claim()


# -- one run -------------------------------------------------------------------


async def run_once(
    *,
    nodes: int,
    difficulty: int,
    args: argparse.Namespace,
    label: str,
    rate: float | None = None,
) -> dict:
    """Start a network, load it, measure the middle, and tear it down."""
    miners = nodes  # everyone mines: the point is to hold the *total* rate fixed
    hashes_per_round = max(1, int(TOTAL_HASHES_PER_SECOND * MINE_INTERVAL / miners))

    config = SimulationConfig(
        num_nodes=nodes,
        strategy="hybrid",
        strategy_kwargs={"fanout": 3},
        max_peers=nodes + 2,
        initial_connections=min(3, nodes - 1),
        tx_interval=0.0,
        tick_interval=1.0,
        seed=args.seed,
        difficulty=difficulty,
        mine_interval=MINE_INTERVAL,
        hashes_per_round=hashes_per_round,
        announce_interval=2.0,
        miners=miners,
    )

    sim = Simulation(config)
    await sim.start()

    bench = ChainBenchmark()
    propagation = PropagationBenchmark()
    observer = sim.communities[0]

    def watch_observer(community, update):
        for outcome in update.applied:
            bench.on_block(outcome.block, outcome.block.index, time.monotonic())

    def watch_propagation(community, update):
        now = time.monotonic()
        for outcome in update.applied:
            propagation.on_block(outcome.block.block_hash, now)

    observer.add_block_listener(watch_observer)
    for community in sim.communities:
        community.add_block_listener(watch_propagation)

    clients = Clients(max(4, nodes), args.seed)
    rng = random.Random(args.seed)
    offered = args.rate if rate is None else rate

    async def offer_load(seconds: float) -> None:
        """Poisson arrivals: exponential gaps at ``args.rate`` per second."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            node = rng.choice(sim.communities)
            identity, action = clients.next_action(node.ledger.state)
            transaction = signed(identity, action)
            if node.submit(transaction):
                bench.on_submit(transaction.tx_hash, time.monotonic())
            await asyncio.sleep(rng.expovariate(offered))

    print(f"  {label}: warm-up {args.warmup:.0f}s…", end="", flush=True)
    await offer_load(args.warmup)

    bench.start_window(time.monotonic())
    print(f" window {args.window:.0f}s…", end="", flush=True)
    await offer_load(args.window)
    bench.end_window(time.monotonic())

    # Drain: stop offering work, let what is queued confirm, then stop the
    # miners so the last block can reach everyone before we look.
    print(f" drain {args.drain:.0f}s…", flush=True)
    await asyncio.sleep(args.drain * 0.7)
    sim.pause_mining()
    await asyncio.sleep(args.drain * 0.3)

    agreement = sim.chain_agreement()
    traffic = aggregate(sim.collect_metrics())
    heights = [c.ledger.height for c in sim.communities]
    await sim.stop()

    return {
        "label": label,
        "nodes": nodes,
        "difficulty": difficulty,
        "offered_rate": offered,
        "hashes_per_round": hashes_per_round,
        **bench.summary(),
        "propagation": propagation.summary(),
        "height": max(heights),
        "stale_block_rate": traffic["stale_block_rate"],
        "reorgs": traffic["reorgs"],
        "avg_reorg_depth": traffic["avg_reorg_depth"],
        "packets_per_node": traffic["avg_packets_sent"],
        "converged": agreement["distinct_heads"] == 1,
        "one_state_root": agreement["distinct_state_roots"] == 1,
    }


# -- reporting -----------------------------------------------------------------


def cell(sample: dict, width: int = 9) -> str:
    """A latency median, or a dash when nothing in the window ever got that deep.

    Printing 0.00 for an empty sample would read as "instant" rather than "no
    data", which is the more damaging of the two lies a benchmark can tell.
    """
    if not sample.get("n"):
        return f"{'—':>{width}}"
    return f"{sample['median']:>{width}.2f}"


def table(rows: list[dict], first: str, first_label: str) -> None:
    base_tps = rows[0]["tx_per_second"] or 1e-9
    print(
        f"\n  {first_label:>6}{'tx/s':>8}{'vs base':>9}{'blk/s':>8}"
        f"{'incl p50':>10}{'k=3 p50':>9}{'k=6 p50':>9}"
        f"{'prop p50':>10}{'stale':>8}{'pkts/node':>11}{'agree':>7}"
    )
    for row in rows:
        prop = row["propagation"]["delay_seconds"]
        prop_cell = f"{prop['median'] * 1000:>7.1f}ms" if prop.get("n") else f"{'—':>9}"
        print(
            f"  {row[first]:>6}{row['tx_per_second']:>8.2f}"
            f"{row['tx_per_second'] / base_tps:>8.2f}x{row['blocks_per_second']:>8.3f}"
            f"{cell(row['inclusion_latency'], 10)}{cell(row['confirm_latency_k3'])}"
            f"{cell(row['confirm_latency_k6'])}{prop_cell:>10}"
            f"{row['stale_block_rate']:>8.1%}{row['packets_per_node']:>11.0f}"
            f"{'yes' if row['converged'] else 'NO':>7}"
        )


def load_table(rows: list[dict]) -> None:
    print(
        f"\n  {'offered':>8}{'confirmed':>11}{'delivered':>11}{'blk/s':>8}{'tx/block':>10}"
        f"{'incl p50':>10}{'incl p95':>10}{'backlog':>9}{'stale':>8}"
    )
    for row in rows:
        submitted = row["submitted"] or 1
        per_block = row["included"] / (
            row["blocks_per_second"] * row["window_seconds"] or 1
        )
        print(
            f"  {row['offered_rate']:>8.0f}{row['tx_per_second']:>11.2f}"
            f"{row['included'] / submitted:>10.0%}{row['blocks_per_second']:>8.3f}"
            f"{per_block:>10.1f}{cell(row['inclusion_latency'], 10)}"
            f"{row['inclusion_latency'].get('p95', 0):>10.2f}{row['unconfirmed']:>9}"
            f"{row['stale_block_rate']:>8.1%}"
        )


async def main() -> None:
    args = parse_args()
    if args.quick:
        args.warmup, args.window, args.drain = 5.0, 12.0, 8.0
        args.nodes = args.nodes[:3]
        args.difficulties = args.difficulties[:3]
        args.rates = args.rates[:3]

    results: dict[str, list[dict]] = {}

    if args.only in (None, "scaling"):
        print(
            f"\n═══ scaling: more nodes, the same {TOTAL_HASHES_PER_SECOND:,} H/s total "
            f"(difficulty {args.base_difficulty}) ═══"
        )
        rows = []
        for n in args.nodes:
            rows.append(
                await run_once(
                    nodes=n,
                    difficulty=args.base_difficulty,
                    args=args,
                    label=f"{n} nodes",
                )
            )
        results["scaling"] = rows
        table(rows, "nodes", "nodes")
        print(
            "\n  Full replication: every node validates everything, so throughput is\n"
            "  bounded by one node's view of the chain and does not grow with the\n"
            "  fleet. What grows is the traffic each node carries."
        )

    if args.only in (None, "difficulty"):
        print(
            f"\n═══ difficulty: the lambda-delta bound "
            f"({args.base_nodes} nodes, {TOTAL_HASHES_PER_SECOND:,} H/s total) ═══"
        )
        rows = []
        for d in sorted(args.difficulties):
            expected = (1 << d) / TOTAL_HASHES_PER_SECOND
            rows.append(
                await run_once(
                    nodes=args.base_nodes,
                    difficulty=d,
                    args=args,
                    label=f"difficulty {d} (block every ~{expected:.1f}s)",
                )
            )
        results["difficulty"] = rows
        table(rows, "difficulty", "diff")
        print(
            "\n  Easier blocks raise the block rate until blocks are found faster than\n"
            "  they spread. Past that point miners extend heads that are already dead\n"
            "  and the extra work is thrown away, which is what the stale column counts."
        )

    if args.only in (None, "load"):
        print(
            f"\n═══ load: offered against delivered "
            f"({args.load_nodes} nodes, difficulty {args.load_difficulty}) ═══"
        )
        rows = []
        for rate in args.rates:
            rows.append(
                await run_once(
                    nodes=args.load_nodes,
                    difficulty=args.load_difficulty,
                    args=args,
                    rate=rate,
                    label=f"{rate:g} tx/s offered",
                )
            )
        results["load"] = rows
        load_table(rows)
        print(
            "\n  Confirmed throughput follows the offered rate until it cannot, and\n"
            "  then flattens: that plateau is the capacity. Past it the surplus does\n"
            "  not vanish, it queues, which is what the backlog and the rising\n"
            "  inclusion latency are showing."
        )

    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nRaw results -> {args.json}")


if __name__ == "__main__":
    asyncio.run(main())
