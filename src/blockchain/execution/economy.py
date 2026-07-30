"""The game's economic rules: tunable constants and the pure math over them.

This module is the *ruleset* of the sweet-potato DApp. It holds no state and
performs no I/O: given the relevant numbers it answers "what does this cost?"
and "how much does this yield?". Keeping it separate from
:mod:`blockchain.execution.state` (what the world looks like) and
:mod:`blockchain.execution.engine` (how a transaction mutates it) means the
economy can be re-balanced, or swapped for tests, without touching consensus
code.

Two invariants make this safe to run inside a consensus-critical path:

**Integer arithmetic only.** Floating point is not bit-identical across
platforms and libm versions, so a single ``float`` in a state transition would
let two honest nodes compute two different states and fork the chain. Every
amount here is an integer in *base units* (see :data:`BONI`) and every rate is
in *basis points* (see :data:`BP`), scaled with ``//`` after multiplying.

**Deterministic randomness.** The game wants surprise (variable harvests,
pests) but consensus forbids ``random``: each validator would roll a different
number. Instead the "dice" are hashes of consensus-visible data (see
:func:`_roll`). Every node re-derives the same value, and nobody can predict it
before the block exists.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from math import isqrt

#: Base units per $BONI. Amounts are integers in base units so that a third of
#: a boniato is representable without floats (500 BONI == 500_000 base units).
BONI = 1_000

#: Basis points denominator: 10_000 bp == 1.0x == 100%.
BP = 10_000

#: Denominator of the crowding factor (see :func:`crowding_scale`).
#:
#: Deliberately far finer than :data:`BP`. The factor is ``1/sqrt(n)`` computed
#: with integer arithmetic, so it is a floor, and the floor's error accumulates
#: ``n`` times over a farm of ``n`` plots. Basis points are too coarse for that:
#: the error would exceed the gain from one more plot and make expansion
#: unprofitable at certain sizes. At 10**-12 resolution the error stays orders of
#: magnitude below the increment for any plausible map size.
CROWDING_SCALE = 10**12

# Domain-separation tags for the deterministic dice. Distinct tags mean the
# harvest roll for a plot can never coincide with the pest roll for it.
_HARVEST_TAG = b"harbourspace-farm-harvest-v1"
_FERTILITY_TAG = b"harbourspace-farm-fertility-v1"
_BLIGHT_TAG = b"harbourspace-farm-blight-v1"


@dataclass(frozen=True)
class Economy:
    """The tunable parameters of the sweet-potato economy.

    Frozen so a running chain cannot have its rules mutated underneath it: a
    re-balance is a new :class:`Economy` instance (in a real chain, a hard
    fork), never an in-place edit.
    """

    # -- the map --------------------------------------------------------------
    #: Plots are laid out on a grid of this width, so plot ``n`` sits at
    #: ``(n % grid_width, n // grid_width)``. Adjacency is therefore geometric
    #: (the four von Neumann neighbours), which is what a player sees on the
    #: frontend, rather than merely "consecutive ids".
    grid_width: int = 16

    # -- onboarding -----------------------------------------------------------
    #: One-time starter grant, so a brand-new key can play without a faucet
    #: server. Deliberately generous enough to buy a second plot eventually.
    starter_balance: int = 500 * BONI
    #: Free plots handed out with the starter kit.
    starter_lands: int = 1

    # -- fees -----------------------------------------------------------------
    #: Burned by every transaction. Boniatos *are* the gas: the ecosystem pays
    #: for its own throughput and the burn counteracts harvest inflation.
    gas_fee: int = 1 * BONI
    #: Burned when planting, on top of gas. This is the seed.
    seed_cost: int = 1 * BONI

    # -- spoilage -------------------------------------------------------------
    #: How many blocks stand for one day. The **only** place real time enters
    #: the chain. Height is the clock everywhere else, so "boniatos last ten
    #: days" has to be converted into a block count somewhere, and doing it
    #: through one named parameter keeps that conversion explicit and tunable
    #: rather than scattered as magic numbers. The default assumes a ten-minute
    #: block, as Bitcoin does.
    blocks_per_day: int = 144
    #: Shelf life of a harvested boniato, in days.
    rot_days: int = 10
    #: Fertilizer recovered per rotted boniato, in basis points. Below
    #: :data:`BP` on purpose: spoilage must be a real loss, otherwise nothing is
    #: at stake in letting a harvest sit and the mechanic is decorative.
    rot_fertilizer_bp: int = 5_000

    # -- fertilizer -----------------------------------------------------------
    #: Blocks of growth removed per whole unit of fertilizer spent.
    growth_blocks_per_fertilizer: int = 10
    #: Floor on how far fertilizer can rush a crop, as a share of the nominal
    #: growth time. Without a floor, a farmer with a compost heap could harvest
    #: on demand in a loop and the growth delay would stop being a constraint at
    #: all. Measured against ``growth_blocks``, not against the crop's current
    #: remaining time, so repeated fertilizing converges on the same floor
    #: instead of halving what is left each time.
    fertilizer_min_growth_bp: int = 5_000

    # -- growing --------------------------------------------------------------
    #: Blocks a crop needs before :class:`~blockchain.execution.actions.Harvest`
    #: is accepted. Block height, not wall-clock time: timestamps are
    #: miner-supplied and therefore not trustworthy for game logic.
    growth_blocks: int = 100

    # -- yields ---------------------------------------------------------------
    #: Inclusive bounds of the raw harvest roll, before any modifier.
    base_yield_min: int = 5 * BONI
    base_yield_max: int = 15 * BONI
    #: Bounds of a plot's innate fertility multiplier (8_000 bp == 0.8x).
    fertility_min_bp: int = 8_000
    fertility_max_bp: int = 12_000
    #: Extra yield per adjacent plot held by the same owner. Rewards building a
    #: contiguous farm instead of scattering plots, which gives the land market
    #: a reason to price neighbours above strangers.
    adjacency_bonus_bp: int = 2_000

    # -- the land market ------------------------------------------------------
    #: Price of the very first plot ever sold.
    genesis_land_price: int = 100 * BONI
    #: Each sale multiplies the price by ``growth_num / growth_den``. The
    #: default (1.047x) takes plot #1 from 100 BONI to roughly 10_000 BONI by
    #: plot #100: land gets scarcer and dearer as the map fills.
    land_price_growth_num: int = 1_047
    land_price_growth_den: int = 1_000

    # -- pests ----------------------------------------------------------------
    #: A blight strikes one plot every this many blocks (0 disables pests).
    blight_interval: int = 200
    #: Fraction of a struck crop that is lost, in basis points.
    blight_penalty_bp: int = 5_000

    def __post_init__(self) -> None:
        if self.grid_width < 1:
            raise ValueError("grid_width must be positive")
        if self.base_yield_min > self.base_yield_max:
            raise ValueError("base_yield_min must not exceed base_yield_max")
        if self.fertility_min_bp > self.fertility_max_bp:
            raise ValueError("fertility_min_bp must not exceed fertility_max_bp")
        if self.land_price_growth_den < 1:
            raise ValueError("land_price_growth_den must be positive")
        if not 0 <= self.blight_penalty_bp <= BP:
            raise ValueError("blight_penalty_bp must be within 0..BP")
        if self.blocks_per_day < 1 or self.rot_days < 1:
            raise ValueError("blocks_per_day and rot_days must be positive")
        if not 0 <= self.rot_fertilizer_bp <= BP:
            raise ValueError("rot_fertilizer_bp must be within 0..BP")
        if self.growth_blocks_per_fertilizer < 1:
            raise ValueError("growth_blocks_per_fertilizer must be positive")
        if not 0 <= self.fertilizer_min_growth_bp <= BP:
            raise ValueError("fertilizer_min_growth_bp must be within 0..BP")

    # -- derived parameters ---------------------------------------------------

    @property
    def rot_blocks(self) -> int:
        """Shelf life of a harvested boniato, in blocks."""
        return self.blocks_per_day * self.rot_days

    @property
    def relief_balance(self) -> int:
        """The grant a destitute farmer may claim to get farming again.

        Derived rather than configured, and pinned to the exact cost of one
        complete cycle: a seed, the gas to plant it, and the gas to harvest it.

        Covering the *whole* cycle rather than just the planting matters. Funding
        only the seed and its gas leaves the farmer at zero again the moment they
        plant, unable to afford the gas for their own harvest, so they would have
        to claim relief a second time mid-cycle to collect the crop they already
        paid for. One grant, one cycle.

        It stops there. A relief large enough to be worth farming would turn
        "zero out your balance and re-claim" into an income, and collecting it
        already requires holding literally nothing, which no profitable strategy
        does. See :meth:`~blockchain.execution.engine.StateMachine._claim` for why
        the chain needs this escape hatch at all once boniatos can rot.
        """
        return 2 * self.gas_fee + self.seed_cost


#: The ruleset a node uses unless told otherwise.
DEFAULT_ECONOMY = Economy()


# -- the map ------------------------------------------------------------------


def coords(land_id: int, economy: Economy = DEFAULT_ECONOMY) -> tuple[int, int]:
    """Grid position ``(x, y)`` of plot ``land_id``."""
    return land_id % economy.grid_width, land_id // economy.grid_width


def neighbours(land_id: int, economy: Economy = DEFAULT_ECONOMY) -> tuple[int, ...]:
    """Ids of the plots orthogonally adjacent to ``land_id``.

    Plots on the left/right edge are not neighbours of the row above or below,
    even though their ids differ by one: the grid does not wrap. Row ``y = 0``
    simply has no northern neighbour, and the southern edge is open because the
    map grows downwards as plots are minted.
    """
    x, y = coords(land_id, economy)
    width = economy.grid_width
    adjacent = []
    if x > 0:
        adjacent.append(land_id - 1)
    if x < width - 1:
        adjacent.append(land_id + 1)
    if y > 0:
        adjacent.append(land_id - width)
    adjacent.append(land_id + width)
    return tuple(sorted(adjacent))


def fertility_of(land_id: int, economy: Economy = DEFAULT_ECONOMY) -> int:
    """This plot's innate yield multiplier, in basis points.

    Derived from the id alone, so every node agrees on a plot's quality without
    storing it anywhere and a buyer can compute what they are about to get
    before spending. The spread is fixed at mint time by geography, not luck of
    the draw at harvest.
    """
    span = economy.fertility_max_bp - economy.fertility_min_bp + 1
    return (
        economy.fertility_min_bp
        + _roll(_FERTILITY_TAG, land_id.to_bytes(8, "big")) % span
    )


# -- the land market ----------------------------------------------------------


def next_land_price(current_price: int, economy: Economy = DEFAULT_ECONOMY) -> int:
    """Price of the plot *after* one has just been sold at ``current_price``.

    A geometric curve computed with integer rationals so it is reproducible.
    ``max`` guards the degenerate case where flooring would stall the curve.
    """
    raised = (
        current_price * economy.land_price_growth_num // economy.land_price_growth_den
    )
    return max(raised, current_price + 1)


# -- yields -------------------------------------------------------------------


def crowding_scale(owner_land_count: int) -> int:
    """Diminishing returns per plot as one farm grows, out of :data:`CROWDING_SCALE`.

    Without this a whale's income scales linearly with capital and the game
    ends: early buyers out-earn everyone forever. Scaling each plot's yield by
    ``1/sqrt(n)`` keeps expansion worthwhile (total income still rises, as
    ``sqrt(n)``) while making the *marginal* plot progressively less profitable,
    so capital eventually prefers a second player over a tenth plot.

    ``math.isqrt`` is exact integer arithmetic, unlike ``math.sqrt``, which would
    put a float in a consensus path. The resolution it is applied at matters as
    much as its exactness, because a coarse floor inverts the incentive it is
    supposed to create. In basis points, ``BP // isqrt(n)`` is a step function
    that pays ``3 * 1.0`` for three plots and ``4 * 0.5`` for four, so a farmer's
    fourth plot would make them **poorer**; smoothing it to
    ``BP**2 // isqrt(n * BP**2)`` merely shrinks the same defect until it
    reappears around 464 plots. Computing the root at :data:`CROWDING_SCALE`
    resolution puts the rounding error far below the marginal gain instead.
    """
    if owner_land_count <= 1:
        return CROWDING_SCALE
    return isqrt(CROWDING_SCALE * CROWDING_SCALE // owner_land_count)


def harvest_amount(
    *,
    entropy: bytes,
    land_id: int,
    tx_hash: bytes,
    fertility_bp: int,
    adjacent_owned: int,
    owner_land_count: int,
    blight_bp: int,
    economy: Economy = DEFAULT_ECONOMY,
) -> int:
    """$BONI minted by harvesting one plot, in base units.

    The modifiers apply in a fixed order, each flooring, so the result is a pure
    function of its arguments:

    1. a raw roll in ``[base_yield_min, base_yield_max]``,
    2. times the plot's innate ``fertility_bp``,
    3. plus ``adjacency_bonus_bp`` for each adjacent plot the owner also holds,
    4. times the crowding penalty for the size of their farm,
    5. minus whatever a blight destroyed.

    ``entropy`` must be a value the harvester cannot choose. The engine passes
    the *previous* block's hash rather than the current one: the current hash
    depends on the nonce the miner is free to grind, which would let a miner
    search for a nonce that fattens their own harvest.
    """
    span = economy.base_yield_max - economy.base_yield_min + 1
    roll = _roll(_HARVEST_TAG, entropy, land_id.to_bytes(8, "big"), tx_hash)
    amount = economy.base_yield_min + roll % span

    amount = amount * fertility_bp // BP
    amount = amount * (BP + economy.adjacency_bonus_bp * adjacent_owned) // BP
    amount = amount * crowding_scale(owner_land_count) // CROWDING_SCALE
    amount = amount * (BP - blight_bp) // BP
    return amount


# -- spoilage and fertilizer --------------------------------------------------


def fertilizer_from_rot(rotted_amount: int, economy: Economy = DEFAULT_ECONOMY) -> int:
    """Fertilizer recovered when ``rotted_amount`` of $BONI spoils."""
    return rotted_amount * economy.rot_fertilizer_bp // BP


def fertilizer_effect(
    *,
    planted_at: int,
    ready_at: int,
    amount: int,
    economy: Economy = DEFAULT_ECONOMY,
) -> tuple[int, int]:
    """What spending ``amount`` of fertilizer on a crop achieves.

    Returns ``(blocks_cut, fertilizer_consumed)``. The consumed figure is derived
    back from the blocks actually bought, so two kinds of waste are refunded
    rather than silently pocketed by the chain:

    - **dust**, an amount too small to buy a whole further block, and
    - **overshoot**, an amount that would push the crop past the floor set by
      ``fertilizer_min_growth_bp``.

    Refunding is not politeness, it is what makes the action safe to use: a
    farmer can offer their whole compost heap without having to compute the
    exact amount that fits, which is the sort of arithmetic a UI should never
    push onto a player.
    """
    floor = planted_at + economy.growth_blocks * economy.fertilizer_min_growth_bp // BP
    headroom = max(0, ready_at - floor)
    requested = amount * economy.growth_blocks_per_fertilizer // BONI
    blocks_cut = min(requested, headroom)
    consumed = blocks_cut * BONI // economy.growth_blocks_per_fertilizer
    return blocks_cut, consumed


# -- pests --------------------------------------------------------------------


def is_blight_block(height: int, economy: Economy = DEFAULT_ECONOMY) -> bool:
    """``True`` iff a blight strikes at this block height."""
    if economy.blight_interval <= 0:
        return False
    return height > 0 and height % economy.blight_interval == 0


def blight_target(entropy: bytes, land_ids: list[int]) -> int | None:
    """Which plot a blight hits, chosen deterministically from ``land_ids``.

    ``land_ids`` must be in a canonical order (the caller sorts them) so that
    every node picks the same victim. Returns ``None`` when there is nothing to
    strike.
    """
    if not land_ids:
        return None
    return land_ids[_roll(_BLIGHT_TAG, entropy) % len(land_ids)]


# -- internals ----------------------------------------------------------------


def _roll(tag: bytes, *parts: bytes) -> int:
    """A deterministic 256-bit "dice roll" over consensus-visible bytes.

    Callers take ``% n`` of the result. The modulo bias is negligible here: the
    hash spans 2**256 while every ``n`` we use is tiny, so no player could
    detect, let alone exploit, the skew.
    """
    h = hashlib.sha256()
    h.update(tag)
    for part in parts:
        h.update(len(part).to_bytes(4, "big"))
        h.update(part)
    return int.from_bytes(h.digest(), "big")
