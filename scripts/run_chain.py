#!/usr/bin/env python3
"""Run a real multi-node chain: competing miners, forks, reorgs and sync.

This is the consensus demo. Everything before it ran either one node with a
chain (``run_api.py``) or many nodes without one (``run_network.py``); here a
fleet of peers mines against each other over IPv8 and has to end up agreeing on
a single history.

What it exercises, in order:

1. **Sunny day.** N peers, of which a few mine. They gossip farm transactions,
   race for blocks, and converge on one head. Forks happen on their own -- two
   miners find a block at the same height, the network briefly disagrees, and
   the heavier branch wins.
2. **Synchronisation.** A node joins after the chain is already tens of blocks
   long, with nothing but the genesis block, and catches up from its neighbours.
3. **Rainy day.** Three forged blocks are injected into a live peer: one with a
   broken Proof-of-Work, one whose transactions do not match its Merkle root,
   and one replaying a transaction that is already on the chain. Each is
   rejected, with the reason printed.

The final report is the one that matters: every node's head, its state root and
its height. Agreement on the head means the fork choice converged; agreement on
the **state root** means every node also computed the same world from it.

Examples
--------
Default run (8 peers, 3 miners, ~40s)::

    python scripts/run_chain.py

More forks: many miners, easy blocks, so blocks are found faster than they
propagate::

    python scripts/run_chain.py --nodes 10 --miners 8 --difficulty 8

A calmer chain, closer to how a real one is tuned::

    python scripts/run_chain.py --difficulty 14 --miners 2
"""

from __future__ import annotations

import _bootstrap  # noqa: F401  (adds src/ to sys.path)

import argparse
import asyncio
import random
import time
from dataclasses import replace

from blockchain.core import Block
from blockchain.crypto import Identity
from blockchain.execution import BuyLand, Claim, Harvest, Plant, signed
from blockchain.metrics import aggregate
from blockchain.simulation import Simulation, SimulationConfig


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--nodes", type=int, default=8, help="peers in the network")
    p.add_argument("--miners", type=int, default=3, help="how many of them mine")
    p.add_argument(
        "--difficulty",
        type=int,
        default=16,
        help="leading zero bits per block. This is the knob that decides whether "
        "the network converges: blocks have to be found more slowly than they "
        "propagate, or every miner is permanently building on a stale head",
    )
    p.add_argument("--duration", type=float, default=40.0, help="mining phase, seconds")
    p.add_argument(
        "--max-peers",
        type=int,
        default=None,
        help="neighbour cap per node (default: everyone can know everyone, so "
        "the demo measures consensus rather than connectivity)",
    )
    p.add_argument("--initial-connections", type=int, default=3)
    p.add_argument(
        "--hashes-per-round",
        type=int,
        default=800,
        help="nonces tried per mining round; with --mine-interval this is a "
        "node's hash rate",
    )
    p.add_argument(
        "--mine-interval", type=float, default=0.1, help="seconds between mining rounds"
    )
    p.add_argument("--farmers", type=int, default=4, help="wallets submitting actions")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--no-join", action="store_true", help="skip the late-joiner phase")
    p.add_argument(
        "--no-reorg", action="store_true", help="skip the competing-branch phase"
    )
    p.add_argument("--no-attack", action="store_true", help="skip the forged blocks")
    return p.parse_args()


# -- the load generator --------------------------------------------------------


class Farmers:
    """A handful of wallets that keep the chain busy with real game actions.

    Actions are chosen against each node's *current* view of the state, the way
    a client would sign them, rather than scripted in advance. Plenty of them
    will be rejected by the time they are mined -- the state moved on -- and that
    is the point: a rejected transaction still pays gas and still shows up in a
    receipt, so the chain is being exercised, not staged.
    """

    def __init__(self, count: int, seed: int) -> None:
        self.identities = [Identity.generate() for _ in range(count)]
        self._rng = random.Random(seed)
        self._claimed: set[bytes] = set()

    def next_action(self, state):
        """Pick a plausible next move for a random farmer."""
        identity = self._rng.choice(self.identities)
        key = identity.public_key

        if key not in self._claimed:
            self._claimed.add(key)
            return identity, Claim()

        mine = state.lands_of(key)
        ready = [p for p in mine if p.is_planted]
        fallow = [p for p in mine if not p.is_planted]
        if ready:
            return identity, Harvest(land_id=self._rng.choice(ready).land_id)
        if fallow:
            return identity, Plant(land_id=self._rng.choice(fallow).land_id)
        if state.balance_of(key) > state.next_land_price:
            return identity, BuyLand()
        return identity, Claim()  # broke: ask the chain for relief


# -- the competing branch ------------------------------------------------------


def competing_branch(ledger, back: int = 2, extra: int = 3) -> list[Block]:
    """Mine a branch that forks ``back`` blocks ago and ends up heavier.

    Forks do happen on their own here -- two miners find the same height and the
    network briefly disagrees -- but they almost never turn into a *reorg*,
    because on loopback a block reaches every peer in well under a millisecond
    and the loser gives up before building on its own block. Waiting for a
    natural deep fork would mean tuning the difficulty until blocks are found
    faster than they spread, which is a different experiment (``--difficulty 8``
    runs it). To show the machinery on demand, we mine the competing branch
    ourselves and hand it to one peer, exactly as a miner returning from a
    network partition would.
    """
    from blockchain.core import mine

    chain = ledger.active_chain()
    fork_point = chain[max(0, len(chain) - 1 - back)]
    branch: list[Block] = []
    parent = fork_point
    for i in range(back + extra):
        block = mine(
            Block.create(
                index=parent.index + 1,
                prev_hash=parent.block_hash,
                transactions=(),
                timestamp=parent.timestamp + 1 + i,
            ),
            ledger.difficulty,
        )
        branch.append(block)
        parent = block
    return branch


# -- the forged blocks ---------------------------------------------------------


def forged_blocks(victim) -> list[tuple[str, Block]]:
    """Three blocks that must not be accepted, one per class of lie."""
    ledger = victim.ledger
    head = ledger.chain.head
    attacker = Identity.generate()

    # 1. No Proof-of-Work: a well-formed block whose nonce was never searched.
    lazy = Block.create(
        index=ledger.height + 1,
        prev_hash=ledger.chain.head_hash,
        transactions=(),
        timestamp=int(time.time()),
        nonce=1,
    )

    # 2. Merkle mismatch: mine an empty block, then stuff a transaction into it
    #    while keeping the header that was actually mined.
    from blockchain.core import mine

    honest = mine(
        Block.create(
            index=ledger.height + 1,
            prev_hash=ledger.chain.head_hash,
            transactions=(),
            timestamp=int(time.time()),
        ),
        ledger.difficulty,
    )
    stuffed = replace(honest, transactions=(signed(attacker, Claim()),))

    # 3. Replay: take a transaction already on the chain and mine it again.
    replayed = None
    for block in reversed(ledger.active_chain()):
        if block.transactions:
            replayed = mine(
                Block.create(
                    index=ledger.height + 1,
                    prev_hash=ledger.chain.head_hash,
                    transactions=(block.transactions[0],),
                    timestamp=int(time.time()),
                ),
                ledger.difficulty,
            )
            break

    attacks = [("no proof-of-work", lazy), ("merkle root does not match", stuffed)]
    if replayed is not None:
        attacks.append(("replays a confirmed transaction", replayed))
    return attacks


# -- reporting -----------------------------------------------------------------


def print_nodes(sim: Simulation) -> None:
    print(
        f"  {'node':<14}{'height':>7}  {'head':<18}{'state root':<18}{'blocks':>7}{'fork':>6}"
    )
    for community in sim.communities:
        ledger = community.ledger
        print(
            f"  {community.config.name:<14}{ledger.height:>7}  "
            f"{ledger.chain.head_hash.hex()[:16]:<18}{ledger.state_root[:16]:<18}"
            f"{len(ledger.chain):>7}{ledger.chain.branch_count():>6}"
        )


async def main() -> None:
    args = parse_args()
    # Room for everyone, including the node that joins later: IPv8 refuses an
    # introduction to a peer that is already at its cap, and a latecomer with no
    # neighbours would look like a synchronisation bug that it is not.
    max_peers = args.max_peers if args.max_peers is not None else args.nodes + 2
    config = SimulationConfig(
        num_nodes=args.nodes,
        strategy="hybrid",
        strategy_kwargs={"fanout": 3},
        max_peers=max_peers,
        initial_connections=args.initial_connections,
        tx_interval=0.0,  # the farmers below generate the load, not dummy traffic
        tick_interval=1.0,
        seed=args.seed,
        difficulty=args.difficulty,
        mine_interval=args.mine_interval,
        hashes_per_round=args.hashes_per_round,
        announce_interval=2.0,
        miners=args.miners,
    )

    # The throughput bound from the lecture, before we run anything: a chain is
    # safe while blocks are found more slowly than they spread. Printing the
    # prediction next to the result is the point of the exercise.
    hash_rate = args.miners * args.hashes_per_round / args.mine_interval
    block_time = (1 << args.difficulty) / hash_rate
    sim = Simulation(config)
    print(
        f"═══ starting {args.nodes} peers, {args.miners} mining at "
        f"difficulty {args.difficulty} ═══"
    )
    print(
        f"  network hash rate ~{hash_rate:,.0f} H/s → expected block every "
        f"{block_time:.1f}s (~{args.duration / block_time:.0f} blocks this run)"
    )
    await sim.start()
    await asyncio.sleep(3)  # let the walker introduce everyone

    farmers = Farmers(args.farmers, args.seed)
    rng = random.Random(args.seed)

    print(f"\n═══ sunny day: {args.duration:.0f}s of mining and farming ═══")
    deadline = time.monotonic() + args.duration
    next_report = time.monotonic() + 5
    while time.monotonic() < deadline:
        # Submit through a random peer, as independent clients would.
        node = rng.choice(sim.communities)
        identity, action = farmers.next_action(node.ledger.state)
        node.submit(signed(identity, action))
        await asyncio.sleep(0.25)

        if time.monotonic() >= next_report:
            agreement = sim.chain_agreement()
            print(
                f"  t+{args.duration - (deadline - time.monotonic()):>5.1f}s  "
                f"height {agreement['min_height']}–{agreement['max_height']}  "
                f"heads {agreement['distinct_heads']}  "
                f"agreement {agreement['agreement']:.0%}"
            )
            next_report += 5

    print("\n  after mining:")
    print_nodes(sim)

    if not args.no_join:
        print("\n═══ synchronisation: a node joins with nothing but genesis ═══")
        latecomer = await sim.join()
        print(
            f"  joined at height {latecomer.ledger.height} (network is at {sim.chain_agreement()['max_height']})"
        )
        for _ in range(20):
            await asyncio.sleep(1)
            if latecomer.ledger.height >= sim.chain_agreement()["max_height"] - 1:
                break
        print(
            f"  caught up to height {latecomer.ledger.height}, "
            f"head {latecomer.ledger.chain.head_hash.hex()[:16]}, "
            f"state root {latecomer.ledger.state_root[:16]}"
        )

    if not args.no_reorg:
        print("\n═══ reorganisation: a heavier branch arrives ═══")
        target = sim.communities[0]
        before_head = target.ledger.chain.head_hash
        before_height = target.ledger.height
        branch = competing_branch(target.ledger)
        print(
            f"  mined {len(branch)} blocks forking {before_height - branch[0].index + 1} "
            f"blocks back, and offered them to {target.config.name}"
        )
        deepest = 0
        returned = 0
        for block in branch:
            update = target.ledger.connect(block)
            deepest = max(deepest, update.reorg_depth)
            returned += len(update.returned)
            if update.head_moved:
                target.metrics.record_block(update.status)
                if update.reorg_depth:
                    target.metrics.record_reorg(update.reorg_depth)
        target._announce_head()
        print(
            f"  {target.config.name}: height {before_height} -> {target.ledger.height}, "
            f"undid {deepest} block(s), {returned} transaction(s) back in the mempool"
        )
        print(
            f"  head changed: {'yes' if target.ledger.chain.head_hash != before_head else 'no'}"
        )
        # The rest of the network hears the announcement and has to follow.
        for _ in range(10):
            await asyncio.sleep(1)
            if sim.chain_agreement()["distinct_heads"] == 1:
                break
        agreement = sim.chain_agreement()
        print(
            f"  network followed: {agreement['agreement']:.0%} of nodes on "
            f"{agreement['majority_head']} at height {agreement['max_height']}"
        )

    if not args.no_attack:
        print("\n═══ rainy day: forged blocks offered to a live peer ═══")
        victim = sim.communities[-1]
        before = victim.ledger.chain.head_hash
        for label, block in forged_blocks(victim):
            update = victim.ledger.connect(block)
            print(f"  {label:<34} -> {update.status:<9} {update.chain.reason}")
        unchanged = victim.ledger.chain.head_hash == before
        print(f"  head unchanged after all three: {'PASS' if unchanged else 'FAIL'}")

    # End phase: stop proposing blocks, then let the tail of the network catch
    # up. Measuring while miners run would time a race rather than test a
    # property -- there is always one block still in flight.
    print("\n═══ settling: miners stopped, waiting for the last blocks ═══")
    sim.pause_mining()
    for _ in range(15):
        await asyncio.sleep(1)
        if sim.chain_agreement()["distinct_heads"] == 1:
            break
    print(f"  settled after {_ + 1}s")

    print("\n═══ final state ═══")
    print_nodes(sim)

    agreement = sim.chain_agreement()
    traffic = aggregate(sim.collect_metrics())
    heights = [c.ledger.height for c in sim.communities]
    await sim.stop()

    print("\n═══ consensus ═══")
    print(f"  distinct heads        {agreement['distinct_heads']}")
    print(f"  distinct state roots  {agreement['distinct_state_roots']}")
    print(f"  agreement on the head {agreement['agreement']:.0%} of nodes")
    print(f"  heights               {min(heights)}–{max(heights)}")
    print(f"  blocks mined          {traffic['blocks_mined']}")
    print(
        f"  reorganisations       {traffic['reorgs']} (avg depth {traffic['avg_reorg_depth']})"
    )
    print(f"  stale block rate      {traffic['stale_block_rate']:.1%}")
    print(f"  packets sent / node   {traffic['avg_packets_sent']:.0f}")

    converged = agreement["distinct_heads"] == 1
    consistent = agreement["distinct_state_roots"] == 1
    print(f"\n  all nodes on one chain:        {'PASS' if converged else 'FAIL'}")
    print(f"  all nodes on one world state:  {'PASS' if consistent else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())
