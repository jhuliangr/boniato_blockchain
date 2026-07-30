#!/usr/bin/env python3
"""Demo: the sweet-potato DApp running on the ledger, end to end.

Plays a whole season of the "$BONI" economy through the real chain: signed
transactions go into blocks, blocks are mined with Proof-of-Work, and the
execution layer turns each block into the next world state. Everything shown
here is the production path, not a simulation of it.

Three farmers, and the season is scripted to walk through every mechanic:

1. **Onboarding** each claims a starter kit. No pre-mine: every boniato in
   existence is created on-chain, in front of you.
2. **Planting** seeds are burned and crops are scheduled by block height.
3. **The land market** a purchase burns its price and the curve steps up.
   Alice buys the plot bordering hers, for the adjacency bonus.
4. **A blight** pests strike a plot deterministically, seeded from the parent
   block's hash.
5. **Harvest** $BONI is minted, scaled by fertility, adjacency and crowding,
   and stamped with an expiry date.
6. **Spoilage** Carol hoards and loses the lot: her batch rots into fertilizer.
   Hoarding has a price, so supply is pushed to circulate.
7. **Relief** rotted down to nothing, Carol would be stuck forever, since
   planting costs boniatos she no longer has. The chain grants her exactly one
   cycle's worth to get going again.
8. **Fertilizer** Alice spends her compost to rush a crop, and gets the
   overshoot refunded.
9. **Determinism** the whole mined chain is replayed on a second, independent
   node, which must arrive at a byte-identical state root. This is the check
   that proves the game logic is consensus-safe.

Each block's transactions are chosen **against the live state**, the way a real
client signs them, rather than scripted up front: the amount of compost Alice has
to spend is not knowable until her harvest has actually rotted.

Examples
--------
Default run::

    python scripts/farm_demo.py

Heavier proof-of-work, slower crops::

    python scripts/farm_demo.py --difficulty 16 --growth-blocks 6
"""

from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse
import hashlib
import time

from blockchain.core import Block, has_proof_of_work, mine
from blockchain.crypto import Identity
from blockchain.execution import (
    BONI,
    BP,
    BuyLand,
    Claim,
    Economy,
    Fertilize,
    Harvest,
    Plant,
    StateMachine,
    Transfer,
    WorldState,
    signed,
)
from blockchain.execution.economy import coords

BAR_WIDTH = 12
SEASON_BLOCKS = 13


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--difficulty",
        type=int,
        default=12,
        help="required leading zero bits per block (higher = slower mining)",
    )
    p.add_argument(
        "--growth-blocks",
        type=int,
        default=4,
        help="blocks a crop needs to mature (100 on the real chain)",
    )
    p.add_argument(
        "--blocks-per-day",
        type=int,
        default=1,
        help=(
            "blocks that stand for one day (144 on the real chain, i.e. a "
            "ten-minute block). One here, so the ten-day shelf life of a "
            "boniato fits inside a demo of a dozen blocks"
        ),
    )
    p.add_argument(
        "--blight-interval",
        type=int,
        default=5,
        help="a pest strikes every N blocks (0 disables pests)",
    )
    p.add_argument(
        "--grid-width",
        type=int,
        default=3,
        help=(
            "plots per row on the map. Narrow on purpose: with three farmers "
            "claiming plots 0, 1 and 2, a width of 3 puts the plot Alice buys "
            "(id 3) directly below her own, which is what makes the adjacency "
            "bonus visible in a thirteen-block demo"
        ),
    )
    return p.parse_args()


# -- formatting ---------------------------------------------------------------


def boni(base_units: int) -> str:
    """Render base units as $BONI with three decimals."""
    return f"{base_units / BONI:,.3f}"


def short(public_key: bytes) -> str:
    """A distinguishable fingerprint of a key.

    Not ``public_key.hex()[:8]``: serialized IPv8 keys begin with a common
    ASN.1 curve header, so every farmer would print the same prefix. Hashing
    first (as :attr:`Identity.address` does) spreads them apart.
    """
    return hashlib.sha1(public_key).hexdigest()[:8]


def bar(progress_bp: int) -> str:
    """A crop-growth bar, the terminal ancestor of the frontend's animation."""
    filled = progress_bp * BAR_WIDTH // BP
    return "▓" * filled + "░" * (BAR_WIDTH - filled)


def print_map(state: WorldState, names: dict[bytes, str], height: int) -> None:
    """Draw the grid: who owns what and how far along each crop is."""
    if not state.farmlands:
        print("  (the map is empty)")
        return
    print(f"  {'plot':>4} {'at':>7}  {'owner':<15} {'fert':>5} {'adj':>4}  crop")
    for land_id in sorted(state.farmlands):
        plot = state.farmlands[land_id]
        x, y = coords(land_id, state.economy)
        crop = (
            f"{bar(plot.growth_progress_bp(height))} "
            + ("READY" if plot.is_ready(height) else f"->{plot.ready_at}")
            + (f"  BLIGHT -{plot.blight_bp // 100}%" if plot.blight_bp else "")
            if plot.is_planted
            else "fallow"
        )
        print(
            f"  {land_id:>4} {f'({x},{y})':>7}  {names[plot.owner]:<15} "
            f"{plot.fertility_bp / BP:>4.2f}x {state.adjacent_owned_count(plot):>4}  {crop}"
        )


def print_larders(state: WorldState, names: dict[bytes, str], height: int) -> None:
    """The perishable inventory: what each farmer holds and when it turns.

    The distinctive part of this chain's accounting. A balance is not one number
    but a queue of dated batches, and the one at the front is the one about to be
    lost, so this is the view a player actually plays against.
    """
    print("  larders")
    for public_key, name in names.items():
        larder = state.larder_of(public_key)
        compost = state.fertilizer_of(public_key)
        batches = (
            "  ".join(
                f"{boni(lot.amount)} rots in {lot.blocks_left(height)}b"
                for lot in larder.lots
            )
            or "empty"
        )
        suffix = f"   compost {boni(compost)}" if compost else ""
        print(f"    {name:<15} {boni(larder.total()):>10} $BONI  |  {batches}{suffix}")


def print_ledger(state: WorldState, names: dict[bytes, str]) -> None:
    """The tokenomics: where every boniato came from and where it went."""
    print("  balances")
    for public_key, name in names.items():
        plots = state.land_count_of(public_key)
        print(
            f"    {name:<15} {boni(state.balance_of(public_key)):>12} $BONI   "
            f"{plots} plot(s)   compost {boni(state.fertilizer_of(public_key))}"
        )
    print(
        f"  supply     minted {boni(state.minted)}   burned {boni(state.burned)}   "
        f"rotted {boni(state.rotted)}   circulating {boni(state.circulating_supply)}"
    )
    print(f"  next plot costs {boni(state.next_land_price)} $BONI")
    print(f"  state root {state.state_root[:24]}…")


# -- the season ---------------------------------------------------------------


def plan_block(height: int, state: WorldState, farmers: dict[str, Identity]) -> list:
    """The transactions to include at ``height``, chosen against live state.

    Deliberately not a static script. Two of the beats cannot be written in
    advance: the compost Alice spends does not exist until her starter kit has
    actually rotted, and its amount depends on how much she had left. A real
    client has the same problem, and solves it the same way, by reading state
    before signing.
    """
    alice, bob, carol = farmers["alice"], farmers["bob"], farmers["carol"]

    if height == 1:
        # Everyone onboards. Carol claims and then does nothing at all, which is
        # the point of her: she is here to hoard.
        return [
            signed(alice, Claim()),
            signed(bob, Claim()),
            signed(carol, Claim()),
            signed(alice, Plant(land_id=0)),
            signed(bob, Plant(land_id=1)),
        ]
    if height == 2:
        # Alice buys the next plot on the map, which on this grid borders her
        # own, so both earn the adjacency bonus. Bob tips her.
        return [
            signed(alice, BuyLand()),
            signed(bob, Transfer(recipient=alice.public_key, amount=25 * BONI)),
        ]
    if height == 3:
        return [signed(alice, Plant(land_id=3))]
    if height == 5:
        return [signed(alice, Harvest(land_id=0))]
    if height == 6:
        # Alice's second plot is not ready yet. Included anyway to show the
        # chain refusing an early harvest and charging gas for the attempt.
        return [signed(bob, Harvest(land_id=1)), signed(alice, Harvest(land_id=3))]
    if height == 7:
        return [signed(alice, Harvest(land_id=3))]
    if height == 10:
        return [signed(alice, Plant(land_id=0))]
    if height == 12:
        # By now the starter kits have rotted. Carol, who hoarded hers, holds
        # nothing but compost and needs relief to farm at all. Alice spends her
        # own compost to rush the crop she planted at block 10.
        actions = [signed(carol, Claim())]
        compost = state.fertilizer_of(alice.public_key)
        if compost:
            actions.append(signed(alice, Fertilize(land_id=0, amount=compost)))
        return actions
    if height == 13:
        # Carol gets going, and Alice collects early thanks to the compost.
        return [signed(carol, Plant(land_id=2)), signed(alice, Harvest(land_id=0))]
    return []


def play_season(
    farmers: dict[str, Identity],
    economy: Economy,
    names: dict[bytes, str],
    difficulty: int,
) -> tuple[list[Block], WorldState]:
    """Mine and execute the season block by block, narrating as it goes.

    Returns the mined chain and the resulting state. The chain is kept so that a
    second node can replay it and check it reaches the same place.
    """
    machine = StateMachine(WorldState.genesis(economy), economy)
    chain = [Block.genesis(timestamp=0)]

    for height in range(1, SEASON_BLOCKS + 1):
        transactions = plan_block(height, machine.state, farmers)
        candidate = Block.create(
            index=height,
            prev_hash=chain[-1].block_hash,
            transactions=tuple(transactions),
            timestamp=int(time.time()),
        )
        block = mine(candidate, difficulty)
        assert has_proof_of_work(block, difficulty)
        assert block.is_valid()
        chain.append(block)

        receipts, events = machine.apply_block(block)

        print(
            f"\n─── block {height}  hash={block.block_id[:12]}… "
            f"({len(block.transactions)} tx)"
        )
        for event in events:
            if event.name == "rot":
                print(
                    f"  ~~ ROT  {names[event.public_key]} loses "
                    f"{boni(event.rotted)} $BONI to spoilage, "
                    f"recovering {boni(event.fertilizer)} compost"
                )
            else:
                print(
                    f"  !! BLIGHT strikes plot {event.land_id}: "
                    f"-{event.penalty_bp // 100}% of the crop"
                )
        for receipt in receipts:
            status = "ok " if receipt.ok else "REJ"
            detail = receipt.detail or receipt.reason
            print(f"  [{status}] {receipt.action:<9} {detail}")
        print_map(machine.state, names, height)
        print_larders(machine.state, names, height)

    return chain, machine.state


def replay(chain: list[Block], economy: Economy) -> WorldState:
    """Execute an already-mined chain from genesis on a fresh node."""
    machine = StateMachine(WorldState.genesis(economy), economy)
    for block in chain[1:]:
        machine.apply_block(block)
    return machine.state


def main() -> None:
    args = parse_args()
    economy = Economy(
        grid_width=args.grid_width,
        growth_blocks=args.growth_blocks,
        blight_interval=args.blight_interval,
        blocks_per_day=args.blocks_per_day,
    )

    farmers = {name: Identity.generate() for name in ("alice", "bob", "carol")}
    names = {
        identity.public_key: f"{name}:{short(identity.public_key)}"
        for name, identity in farmers.items()
    }

    print("🍠  BONIATO CHAIN  a crop-to-earn DApp on the ledger\n")
    print(f"  farmers        {', '.join(names.values())}")
    print(
        f"  economy        seed {boni(economy.seed_cost)} $BONI, gas "
        f"{boni(economy.gas_fee)} $BONI, crops mature in {economy.growth_blocks} blocks"
    )
    print(
        f"  shelf life     {economy.rot_days} days = {economy.rot_blocks} blocks, "
        f"then {economy.rot_fertilizer_bp // 100}% comes back as compost"
    )
    print(
        f"  compost        {economy.growth_blocks_per_fertilizer} blocks of growth per "
        f"unit, never below {economy.fertilizer_min_growth_bp // 100}% of a crop's time"
    )
    print(
        f"  pests          every {economy.blight_interval or '-'} blocks, "
        f"-{economy.blight_penalty_bp // 100}% of the struck crop"
    )
    print(f"  proof-of-work  {args.difficulty} leading zero bits per block")

    started = time.perf_counter()
    chain, state = play_season(farmers, economy, names, args.difficulty)
    print(
        f"\nmined and executed {len(chain) - 1} blocks in {time.perf_counter() - started:.2f}s"
    )

    print("\n═══ end of season ═══")
    print_ledger(state, names)

    print("\n  leaderboard")
    for rank, row in enumerate(state.leaderboard(limit=5), start=1):
        key = bytes.fromhex(row["public_key"])
        holder = names.get(key, short(key))
        print(
            f"    {rank}. {holder:<15} {boni(row['balance']):>12} $BONI  "
            f"{row['plots']} plot(s)  compost {boni(row['fertilizer'])}"
        )

    # -- Determinism: a second node replays the same chain from genesis. -------
    # If the game logic used `random`, a wall clock or a float anywhere, the two
    # state roots would differ here and the network would have forked.
    print("\n═══ consensus check ═══")
    replayed = replay(chain, economy)
    agrees = replayed.state_hash == state.state_hash
    print(f"  node A state root  {state.state_root}")
    print(f"  node B state root  {replayed.state_root}")
    print(f"  independent replay agrees: {'PASS' if agrees else 'FAIL'}")

    # The supply invariant: no boniato appears or vanishes unaccounted for.
    # Spoilage is a third sink alongside minting and burning, so it belongs here.
    balanced = state.circulating_supply == state.minted - state.burned - state.rotted
    print(
        "  supply invariant (circulating == minted - burned - rotted): "
        f"{'PASS' if balanced else 'FAIL'}"
    )


if __name__ == "__main__":
    main()
