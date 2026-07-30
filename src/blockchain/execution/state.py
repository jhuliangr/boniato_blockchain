"""The world state: everything the chain remembers about the farm.

This is the *account model*, chosen over UTXOs deliberately. A UTXO set answers
"who can spend what" beautifully but has no natural place to hang the mutable,
long-lived facts a game needs ("this plot is 40 blocks into growing"). An
account/state model, like Ethereum's, stores those facts directly, so the state
transition is a readable mutation instead of a coin-shuffling trick.

Contents:

- ``larders``: public key -> :class:`Larder`, the account's holdings of $BONI.
- ``fertilizer``: public key -> fertilizer base units, the compost from spoilage.
- ``farmlands``: plot id -> :class:`Farmland`, the authoritative record of who
  owns what and what is growing on it.
- ``next_land_price``: where the land curve currently sits.
- ``burned`` / ``minted`` / ``rotted``: cumulative supply flows, so the
  tokenomics are auditable rather than asserted.

Boniatos **perish**, and that single requirement is what shapes this module. A
balance cannot be one integer, because the chain has to know *when* each boniato
was harvested in order to know when it spoils. So an account holds a
:class:`Larder` of dated lots instead, which lands somewhere between the two
classic models: account-shaped for the game's mutable facts, UTXO-shaped for the
money itself. The lots are the interesting part of the design; the rest of the
state is ordinary.

Two properties matter more than convenience here:

**Canonical serialization.** :attr:`WorldState.state_hash` folds the whole state
into 32 bytes over deterministically ordered, length-prefixed fields. Two nodes
that processed the same blocks get the same hash, so divergence is detectable
immediately instead of silently. This is the value a block header should commit
to as its ``state_root``.

**Ownership has one home.** A plot's owner lives on the :class:`Farmland` and
nowhere else. Per-owner lookups go through an index that this class maintains,
never through a second copy of the truth that could drift out of sync.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Iterable

from blockchain.execution.economy import (
    DEFAULT_ECONOMY,
    BP,
    Economy,
    fertility_of,
    fertilizer_from_rot,
    neighbours,
)

_STATE_TAG = b"harbourspace-state-v1"


@dataclass(frozen=True)
class Lot:
    """A batch of boniatos harvested together, and therefore spoiling together.

    ``expires_at`` is the height at which the batch is compost: it is spendable
    while ``height < expires_at``. Frozen, because a lot is never edited; it is
    split, consumed or thrown away.
    """

    amount: int
    expires_at: int

    def blocks_left(self, height: int) -> int:
        """Blocks remaining before this batch rots (0 once it has)."""
        return max(0, self.expires_at - height)


class Larder:
    """An account's perishable $BONI, ordered soonest-to-rot first.

    The ordering is the whole point. Spending takes from the front, so a farmer
    always spends the boniatos closest to spoiling and keeps the fresh ones. That
    is both what a person would do with a real larder and the only policy that
    does not quietly destroy value: last-in-first-out would let the oldest lot
    rot while newer ones are spent around it.

    Lots sharing an expiry are merged, which keeps the structure compact and,
    more importantly, **canonical**: two nodes that arrived at the same holdings
    by different routes hold byte-identical larders, so
    :attr:`WorldState.state_hash` agrees.
    """

    def __init__(self, lots: Iterable[Lot] = ()) -> None:
        self._lots: list[Lot] = []
        for lot in lots:
            self.add(lot.amount, lot.expires_at)

    # -- reads ----------------------------------------------------------------

    @property
    def lots(self) -> tuple[Lot, ...]:
        """The batches held, soonest to rot first."""
        return tuple(self._lots)

    def total(self) -> int:
        """Spendable balance in base units."""
        return sum(lot.amount for lot in self._lots)

    def __len__(self) -> int:
        return len(self._lots)

    def __bool__(self) -> bool:
        return bool(self._lots)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Larder) and self._lots == other._lots

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"Larder(total={self.total()}, lots={len(self._lots)})"

    # -- writes ---------------------------------------------------------------

    def add(self, amount: int, expires_at: int) -> None:
        """Deposit ``amount``, spoiling at ``expires_at``."""
        if amount < 0:
            raise ValueError("lot amount must be non-negative")
        if amount == 0:
            return
        for index, lot in enumerate(self._lots):
            if lot.expires_at == expires_at:
                self._lots[index] = Lot(lot.amount + amount, expires_at)
                return
            if lot.expires_at > expires_at:
                self._lots.insert(index, Lot(amount, expires_at))
                return
        self._lots.append(Lot(amount, expires_at))

    def take(self, amount: int) -> tuple[Lot, ...]:
        """Remove ``amount``, oldest first, returning the lots consumed.

        The consumed lots are returned rather than just their total because a
        transfer has to carry their **expiry dates** to the recipient. Handing
        over fresh boniatos instead would make the whole mechanic bypassable:
        anyone could launder a spoiling batch by passing it between two of their
        own keys.

        Raises on an overdraft, for the same reason :meth:`WorldState.debit`
        does: affordability is the engine's job to check beforehand.
        """
        if amount < 0:
            raise ValueError("take amount must be non-negative")
        if amount > self.total():
            raise ValueError("insufficient balance")

        taken: list[Lot] = []
        remaining = amount
        while remaining > 0:
            lot = self._lots[0]
            if lot.amount <= remaining:
                taken.append(lot)
                remaining -= lot.amount
                self._lots.pop(0)
            else:
                taken.append(Lot(remaining, lot.expires_at))
                self._lots[0] = Lot(lot.amount - remaining, lot.expires_at)
                remaining = 0
        return tuple(taken)

    def expire(self, height: int) -> int:
        """Discard every lot that has spoiled by ``height``, returning the loss.

        Cheap despite running on every account every block: lots are sorted by
        expiry, so this stops at the first survivor.
        """
        lost = 0
        while self._lots and self._lots[0].expires_at <= height:
            lost += self._lots.pop(0).amount
        return lost


@dataclass
class Farmland:
    """One plot of land: an owned, plantable, harvestable square of the map.

    Mutable by design. Plots are the part of the world that changes, and making
    them frozen would mean rebuilding the dict on every plant and harvest for no
    safety gain, since :class:`WorldState` is already the mutation boundary.
    """

    land_id: int
    owner: bytes
    #: Innate yield multiplier in basis points, derived from ``land_id``.
    fertility_bp: int
    #: Height at which the current crop was planted (0 when fallow).
    planted_at: int = 0
    #: Height from which the crop may be harvested (0 when fallow).
    ready_at: int = 0
    #: Share of the current crop destroyed by pests, in basis points.
    blight_bp: int = 0

    @property
    def is_planted(self) -> bool:
        return self.ready_at > 0

    def is_ready(self, height: int) -> bool:
        """``True`` iff a crop is growing here and its time has come."""
        return self.is_planted and height >= self.ready_at

    def growth_progress_bp(self, height: int) -> int:
        """How far along the crop is, in basis points, for the UI's animation."""
        if not self.is_planted:
            return 0
        span = self.ready_at - self.planted_at
        if span <= 0:
            return BP
        elapsed = max(0, min(height - self.planted_at, span))
        return elapsed * BP // span

    def clear_crop(self) -> None:
        """Return the plot to fallow, discarding any blight with the crop."""
        self.planted_at = 0
        self.ready_at = 0
        self.blight_bp = 0


@dataclass
class WorldState:
    """The mutable state the chain's transactions operate on."""

    economy: Economy = DEFAULT_ECONOMY
    larders: dict[bytes, Larder] = field(default_factory=dict)
    #: Compost recovered from spoiled boniatos. Non-perishable (it is already
    #: rotten) and non-transferable: it is a byproduct of a farm, not a currency,
    #: and leaving it unmovable keeps the instruction set small.
    fertilizer: dict[bytes, int] = field(default_factory=dict)
    farmlands: dict[int, Farmland] = field(default_factory=dict)
    next_land_price: int = 0
    #: Public keys that have already taken the one-time starter kit. Tracked
    #: explicitly rather than inferred from "has a balance", so that receiving a
    #: transfer before claiming does not silently forfeit a new player's kit.
    claimed: set[bytes] = field(default_factory=set)
    burned: int = 0
    minted: int = 0
    #: $BONI lost to spoilage. Kept apart from ``burned`` because the two say
    #: different things about the economy: one is the price of using the chain,
    #: the other is the price of hoarding.
    rotted: int = 0
    fertilizer_minted: int = 0

    def __post_init__(self) -> None:
        if self.next_land_price == 0:
            self.next_land_price = self.economy.genesis_land_price

    @classmethod
    def genesis(cls, economy: Economy = DEFAULT_ECONOMY) -> "WorldState":
        """The state every node starts from: an empty map, no pre-mine.

        There are no privileged balances at genesis. Every boniato in existence
        was either claimed as a starter kit or grown, which makes the supply
        curve entirely explainable from the chain itself.
        """
        return cls(economy=economy, next_land_price=economy.genesis_land_price)

    # -- balances -------------------------------------------------------------

    def larder_of(self, public_key: bytes) -> Larder:
        """The account's lots. Unknown keys get an empty larder, not an error.

        The returned larder is *not* attached to the state, so mutating it does
        nothing for an account that does not exist yet. Deposits go through
        :meth:`credit` / :meth:`deposit`, which create the account.
        """
        return self.larders.get(public_key, Larder())

    def balance_of(self, public_key: bytes) -> int:
        """Spendable balance in base units, summed across the account's lots."""
        return self.larder_of(public_key).total()

    def fertilizer_of(self, public_key: bytes) -> int:
        return self.fertilizer.get(public_key, 0)

    def credit(self, public_key: bytes, amount: int, expires_at: int) -> None:
        """Add a batch of ``amount`` boniatos spoiling at ``expires_at``."""
        if amount < 0:
            raise ValueError("credit amount must be non-negative")
        if amount == 0:
            return
        self.larders.setdefault(public_key, Larder()).add(amount, expires_at)

    def deposit(self, public_key: bytes, lots: Iterable[Lot]) -> None:
        """Add whole lots, preserving their expiry dates. Used by transfers."""
        for lot in lots:
            self.credit(public_key, lot.amount, lot.expires_at)

    def debit(self, public_key: bytes, amount: int) -> tuple[Lot, ...]:
        """Remove ``amount``, soonest-to-rot first, returning the lots consumed.

        Raises on insufficient funds. This is an *assertion*, not the place to
        reject a transaction: the engine checks affordability up front so that a
        failing transaction never reaches a half-applied state. Reaching here
        with too little means a bug in that check, and crashing beats minting
        money out of a negative balance.
        """
        larder = self.larders.get(public_key)
        if larder is None:
            if amount == 0:
                return ()
            raise ValueError("insufficient balance")
        taken = larder.take(amount)
        if not larder:
            # Drop empty accounts so the state hash does not depend on the
            # history of who once held a balance.
            self.larders.pop(public_key, None)
        return taken

    def burn(self, public_key: bytes, amount: int) -> None:
        """Take ``amount`` out of circulation permanently."""
        self.debit(public_key, amount)
        self.burned += amount

    def mint(self, public_key: bytes, amount: int, expires_at: int) -> None:
        """Create ``amount`` and credit it. The only source of new supply.

        Every boniato is born with an expiry, including the starter kit: there is
        no such thing as an imperishable boniato, so there is no overload of this
        method that omits the date.
        """
        self.credit(public_key, amount, expires_at)
        self.minted += amount

    def spoil(self, height: int) -> list[tuple[bytes, int, int]]:
        """Rot every expired lot, converting the loss into fertilizer.

        Returns ``(public_key, rotted, fertilizer_gained)`` per affected account,
        ordered by public key so the caller's report is deterministic too.

        Applied eagerly, once per block, rather than lazily when a balance is
        read. Laziness would be cheaper but would let two nodes hold different
        raw lot lists for the same effective balance, and
        :attr:`state_hash` commits to the lots, so their hashes would diverge
        even though the nodes agreed on every balance.
        """
        report: list[tuple[bytes, int, int]] = []
        for public_key in sorted(self.larders):
            larder = self.larders[public_key]
            lost = larder.expire(height)
            if not lost:
                continue
            self.rotted += lost
            gained = fertilizer_from_rot(lost, self.economy)
            if gained:
                self.fertilizer[public_key] = self.fertilizer_of(public_key) + gained
                self.fertilizer_minted += gained
            report.append((public_key, lost, gained))

        for public_key, _, _ in report:
            if not self.larders.get(public_key):
                self.larders.pop(public_key, None)
        return report

    def spend_fertilizer(self, public_key: bytes, amount: int) -> None:
        """Consume fertilizer. Raises on overdraft, as :meth:`debit` does."""
        if amount < 0:
            raise ValueError("fertilizer amount must be non-negative")
        current = self.fertilizer_of(public_key)
        if current < amount:
            raise ValueError("insufficient fertilizer")
        remaining = current - amount
        if remaining:
            self.fertilizer[public_key] = remaining
        else:
            self.fertilizer.pop(public_key, None)

    @property
    def circulating_supply(self) -> int:
        """Total $BONI held by accounts. Equals ``minted - burned - rotted``."""
        return sum(larder.total() for larder in self.larders.values())

    # -- land -----------------------------------------------------------------

    def mint_land(self, owner: bytes) -> Farmland:
        """Create the next plot on the map and assign it to ``owner``.

        Ids are handed out sequentially from 0, which is what makes the grid fill
        row by row and keeps :func:`~blockchain.execution.economy.neighbours`
        meaningful.
        """
        land_id = len(self.farmlands)
        plot = Farmland(
            land_id=land_id,
            owner=owner,
            fertility_bp=fertility_of(land_id, self.economy),
        )
        self.farmlands[land_id] = plot
        return plot

    def lands_of(self, public_key: bytes) -> list[Farmland]:
        """Every plot ``public_key`` owns, ordered by id."""
        return [
            plot
            for _, plot in sorted(self.farmlands.items())
            if plot.owner == public_key
        ]

    def land_count_of(self, public_key: bytes) -> int:
        return sum(1 for plot in self.farmlands.values() if plot.owner == public_key)

    def adjacent_owned_count(self, plot: Farmland) -> int:
        """How many of ``plot``'s neighbours its owner also holds.

        Drives the adjacency bonus: a contiguous farm out-earns the same number
        of scattered plots.
        """
        return sum(
            1
            for neighbour_id in neighbours(plot.land_id, self.economy)
            if (neighbour := self.farmlands.get(neighbour_id)) is not None
            and neighbour.owner == plot.owner
        )

    # -- commitment -----------------------------------------------------------

    @property
    def state_hash(self) -> bytes:
        """A 32-byte commitment to the entire state.

        Everything consensus-visible goes in, in a canonical order (balances
        sorted by key, plots by id) with every variable-length field
        length-prefixed, so the encoding is unambiguous. A block header can carry
        this as its ``state_root``, which is what turns "I validated your block"
        into "I agree with the state your block produces".

        A production chain would use a Merkle-Patricia trie so a light client
        could prove one account without the whole state. Here the state is small
        enough that a flat digest is honest about its purpose, and
        :class:`~blockchain.core.merkle.MerkleTree` already demonstrates the
        proof machinery over transactions.
        """
        h = hashlib.sha256()
        h.update(_STATE_TAG)

        # Lots, not just totals: the expiry dates are consensus state, since they
        # decide what spoils next block and what a transfer hands over.
        h.update(len(self.larders).to_bytes(4, "big"))
        for public_key in sorted(self.larders):
            lots = self.larders[public_key].lots
            h.update(len(public_key).to_bytes(2, "big"))
            h.update(public_key)
            h.update(len(lots).to_bytes(4, "big"))
            for lot in lots:
                h.update(lot.amount.to_bytes(16, "big"))
                h.update(lot.expires_at.to_bytes(8, "big"))

        h.update(len(self.fertilizer).to_bytes(4, "big"))
        for public_key in sorted(self.fertilizer):
            h.update(len(public_key).to_bytes(2, "big"))
            h.update(public_key)
            h.update(self.fertilizer[public_key].to_bytes(16, "big"))

        h.update(len(self.farmlands).to_bytes(4, "big"))
        for land_id in sorted(self.farmlands):
            plot = self.farmlands[land_id]
            h.update(land_id.to_bytes(4, "big"))
            h.update(len(plot.owner).to_bytes(2, "big"))
            h.update(plot.owner)
            h.update(plot.fertility_bp.to_bytes(4, "big"))
            h.update(plot.planted_at.to_bytes(8, "big"))
            h.update(plot.ready_at.to_bytes(8, "big"))
            h.update(plot.blight_bp.to_bytes(4, "big"))

        h.update(len(self.claimed).to_bytes(4, "big"))
        for public_key in sorted(self.claimed):
            h.update(len(public_key).to_bytes(2, "big"))
            h.update(public_key)

        h.update(self.next_land_price.to_bytes(16, "big"))
        h.update(self.burned.to_bytes(16, "big"))
        h.update(self.minted.to_bytes(16, "big"))
        h.update(self.rotted.to_bytes(16, "big"))
        h.update(self.fertilizer_minted.to_bytes(16, "big"))
        return h.digest()

    @property
    def state_root(self) -> str:
        """Hex form of :attr:`state_hash`, for logs and block headers."""
        return self.state_hash.hex()

    # -- reporting ------------------------------------------------------------

    def summary(self) -> dict:
        """A snapshot for reports, the REST layer and the leaderboard."""
        return {
            "accounts": len(self.larders),
            "plots": len(self.farmlands),
            "planted": sum(1 for plot in self.farmlands.values() if plot.is_planted),
            "next_land_price": self.next_land_price,
            "circulating_supply": self.circulating_supply,
            "minted": self.minted,
            "burned": self.burned,
            "rotted": self.rotted,
            "fertilizer_minted": self.fertilizer_minted,
            "state_root": self.state_root[:16],
        }

    def accounts(self) -> list[bytes]:
        """Every key the state knows about, in canonical order.

        A holder of nothing but fertilizer is still an account: their boniatos
        rotted, and they are exactly the player the UI must not lose track of.
        """
        return sorted(set(self.larders) | set(self.fertilizer))

    def leaderboard(self, limit: int = 10) -> list[dict]:
        """Richest farmers first, ranked by $BONI then by plots held.

        Ties break on public key so the ordering is total and every node renders
        the same podium.
        """
        ranked = sorted(
            self.accounts(),
            key=lambda key: (-self.balance_of(key), -self.land_count_of(key), key),
        )
        return [
            {
                "public_key": public_key.hex(),
                "balance": self.balance_of(public_key),
                "plots": self.land_count_of(public_key),
                "fertilizer": self.fertilizer_of(public_key),
            }
            for public_key in ranked[:limit]
        ]
