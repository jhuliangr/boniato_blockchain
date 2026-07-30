"""The state transition function: how a block turns one state into the next.

This is the execution layer of the stack the course slides describe
(infrastructure -> consensus -> **execution** -> access -> application). Given a
:class:`~blockchain.core.block.Block` and a :class:`WorldState`, the
:class:`StateMachine` produces the state that every honest node must agree on.

Three properties are non-negotiable, because a violation of any of them is a
chain split rather than a bug:

**Determinism.** No clock, no ``random``, no floats, no iteration over unordered
sets. Variability comes from hashes of consensus-visible bytes (see
:mod:`blockchain.execution.economy`), and the only ordering that matters is the
one the block itself fixes.

**All-or-nothing per transaction.** Every precondition is checked *before* any
mutation, so a rejected transaction leaves the state untouched. A half-applied
transfer would corrupt the supply invariant permanently. That is why
:meth:`WorldState.debit` raises instead of returning ``False``: by the time it
runs, affordability has already been established.

**Failure is not an error.** An invalid action does not throw; it produces a
:class:`Receipt` with ``ok=False`` and a reason. A miner cannot be trusted to
only include valid transactions, and a node must be able to process a block
containing rubbish without crashing. Only the gas is taken.

Block height is the clock. Timestamps are miner-supplied and therefore not
trustworthy for game logic: a miner could claim a crop grew for a year.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from blockchain.core.block import Block
from blockchain.core.transaction import Transaction
from blockchain.execution import economy as econ
from blockchain.execution.actions import (
    Action,
    BuyLand,
    Claim,
    Fertilize,
    Harvest,
    Plant,
    Transfer,
    decode,
)
from blockchain.execution.economy import DEFAULT_ECONOMY, Economy
from blockchain.execution.state import Farmland, WorldState


@dataclass(frozen=True)
class BlockContext:
    """The block-scoped facts a transaction is allowed to depend on.

    ``entropy`` is the *previous* block's hash, never the current one. The
    current hash contains the nonce the miner is free to grind, so seeding a
    harvest with it would let a miner search for the nonce that pays them best.
    The parent hash is already fixed and unknown to anyone signing a transaction
    for the next block, which is exactly the property a fair dice needs.
    """

    height: int
    entropy: bytes

    @classmethod
    def of(cls, block: Block) -> "BlockContext":
        return cls(height=block.index, entropy=block.prev_hash)


@dataclass(frozen=True)
class Receipt:
    """The outcome of executing one transaction.

    Receipts are the audit trail the REST layer and the demo read back. They are
    *not* consensus data here: nothing commits to a receipts root, so a node
    keeps them purely for observability.
    """

    tx_id: str
    action: str
    ok: bool
    reason: str = ""
    gas_burned: int = 0
    minted: int = 0
    burned: int = 0
    #: Action-specific detail, e.g. which plot was harvested for how much.
    detail: dict = field(default_factory=dict)
    #: Who signed the transaction. Stamped centrally in
    #: :meth:`StateMachine.apply_transaction` rather than at each construction
    #: site, so no receipt can be built without it. Without an attributable
    #: signer, a feed of receipts cannot be filtered or read as "what did *I*
    #: just do", which is most of what makes a chain legible to a player.
    public_key: bytes = b""

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        status = "ok" if self.ok else f"rejected({self.reason})"
        return f"Receipt({self.action}, {status}, tx={self.tx_id[:10]}…)"


@dataclass(frozen=True)
class BlightEvent:
    """A pest strike: a system event, not something any account requested."""

    height: int
    land_id: int
    penalty_bp: int

    name = "blight"


@dataclass(frozen=True)
class RotEvent:
    """A batch of boniatos spoiled and turned into compost.

    Also a system event: nobody signs for their harvest going off. It happens to
    an account because time passed, which is why it is resolved at the top of a
    block rather than in response to a transaction.
    """

    height: int
    public_key: bytes
    rotted: int
    fertilizer: int

    name = "rot"


#: What the chain did to the world of its own accord this block.
SystemEvent = BlightEvent | RotEvent


class StateMachine:
    """Applies transactions and system events to a :class:`WorldState`."""

    def __init__(
        self, state: WorldState | None = None, economy: Economy = DEFAULT_ECONOMY
    ) -> None:
        self.economy = economy
        self.state = state if state is not None else WorldState.genesis(economy)
        # Precondition check per action type. Claim is absent on purpose: it is
        # gas-exempt and handled before this table is consulted.
        self._checks = {
            Transfer: self._transfer,
            BuyLand: self._buy_land,
            Plant: self._plant,
            Harvest: self._harvest,
            Fertilize: self._fertilize,
        }

    # -- block level ----------------------------------------------------------

    def apply_block(self, block: Block) -> tuple[list[Receipt], list[SystemEvent]]:
        """Execute a whole block: system events first, then its transactions.

        Both kinds of system effect are resolved *before* the transactions:

        - **Spoilage**, so that a transaction cannot spend boniatos that expired
          at this very height. Settling accounts with the calendar first is also
          what lets the rest of the engine treat every lot it sees as fresh.
        - **Pests**, so that a harvest included in the same block sees the
          damage. The alternative would let a player who watches the mempool
          escape a blight by racing a harvest into the very block that triggers
          it, which drains the mechanic of its tension.

        The caller is responsible for having validated the block's structure and
        Proof-of-Work first: this method assumes the block is one the chain
        accepts and only decides what it *means*.
        """
        context = BlockContext.of(block)
        events = self._apply_system_events(context)
        receipts = [self.apply_transaction(tx, context) for tx in block.transactions]
        return receipts, events

    def _apply_system_events(self, context: BlockContext) -> list[SystemEvent]:
        """Run the chain's own scheduled effects for this height."""
        events: list[SystemEvent] = [
            RotEvent(context.height, public_key, rotted, fertilizer)
            for public_key, rotted, fertilizer in self.state.spoil(context.height)
        ]
        events.extend(self._apply_blight(context))
        return events

    def _apply_blight(self, context: BlockContext) -> list[BlightEvent]:
        if not econ.is_blight_block(context.height, self.economy):
            return []
        planted = sorted(
            land_id for land_id, plot in self.state.farmlands.items() if plot.is_planted
        )
        target = econ.blight_target(context.entropy, planted)
        if target is None:
            return []
        plot = self.state.farmlands[target]
        # Cap rather than accumulate: two blights on one crop cannot destroy more
        # than the crop, and a saturating penalty keeps the yield formula sane.
        plot.blight_bp = min(econ.BP, plot.blight_bp + self.economy.blight_penalty_bp)
        return [BlightEvent(context.height, target, plot.blight_bp)]

    # -- helpers --------------------------------------------------------------

    def _expiry_at(self, height: int) -> int:
        """When boniatos minted at ``height`` will spoil."""
        return height + self.economy.rot_blocks

    # -- transaction level ----------------------------------------------------

    def apply_transaction(self, tx: Transaction, context: BlockContext) -> Receipt:
        """Execute one transaction, returning its receipt.

        Never raises on bad input. The signer is stamped on the way out, in one
        place, so that no path through the engine can produce an unattributed
        receipt.
        """
        return replace(self._apply(tx, context), public_key=tx.public_key)

    def _apply(self, tx: Transaction, context: BlockContext) -> Receipt:
        """Decide what a transaction does.

        The order of checks matters: signature, then decoding, then gas, then
        action preconditions. A transaction whose signature does not verify is
        not chargeable, because we cannot attribute it to anyone. (Its receipt
        still carries the public key it *claimed*, which is all we know.)
        """
        if not tx.is_valid():
            return Receipt(tx.tx_id, "invalid", ok=False, reason="bad signature")

        if not tx.action:
            # A phase-2 dummy transaction: it carries no intent, so it changes no
            # state. Kept accepted rather than rejected so the gossip
            # experiments keep working against a farming chain unchanged.
            return Receipt(tx.tx_id, "noop", ok=True, reason="no action")

        action = decode(tx.action)
        if action is None:
            return Receipt(tx.tx_id, "unknown", ok=False, reason="undecodable action")

        return self._execute(tx, action, context)

    def _execute(
        self, tx: Transaction, action: Action, context: BlockContext
    ) -> Receipt:
        sender = tx.public_key

        # Claim is the one gas-exempt action: a fresh key has no boniatos, so
        # charging it for onboarding would make the chain unjoinable.
        if isinstance(action, Claim):
            return self._claim(tx, sender, context)

        gas = self.economy.gas_fee
        if self.state.balance_of(sender) < gas:
            return Receipt(tx.tx_id, action.name, ok=False, reason="cannot afford gas")

        # Preconditions are evaluated *before* the gas is taken, so each check
        # reasons about the pre-gas balance and subtracts the fee itself. Then the
        # sender pays for the attempt whatever the verdict: charging rejected
        # transactions is what stops a peer from spamming the chain with cheap
        # failures, since block space costs money even when it buys nothing.
        rejection = self._checks[type(action)](sender, action, context)
        self.state.burn(sender, gas)
        if rejection is not None:
            return Receipt(
                tx.tx_id, action.name, ok=False, reason=rejection, gas_burned=gas
            )
        return self._commit(tx, action, sender, context, gas)

    # -- actions --------------------------------------------------------------
    #
    # Each handler is split in two: a check that returns a rejection reason (or
    # ``None`` to mean "this will succeed"), and the mutation that follows. The
    # split is what guarantees a rejected action mutates nothing.

    def _claim(self, tx: Transaction, sender: bytes, context: BlockContext) -> Receipt:
        """Onboard a new key, or bail out a farmer who has nothing left.

        Two jobs in one action, because the second only exists as a consequence
        of the first plus spoilage. Once boniatos rot, a farmer can be left with
        an empty larder, a fallow plot and no way back: planting costs a seed and
        gas, both denominated in the boniatos they no longer have, so the account
        is bricked permanently. Fertilizer does not help, since fertilizing also
        costs gas and needs a growing crop to act on.

        So a destitute account may claim again, for exactly
        :attr:`Economy.relief_balance` and no land. Pinning the relief to the
        cost of one planting is what stops it becoming an income: to collect it
        you must first hold literally zero, which no profitable strategy does.
        """
        first_time = sender not in self.state.claimed
        if not first_time and self.state.balance_of(sender) > 0:
            return Receipt(tx.tx_id, "claim", ok=False, reason="already claimed")

        expires_at = self._expiry_at(context.height)
        if not first_time:
            self.state.mint(sender, self.economy.relief_balance, expires_at)
            return Receipt(
                tx.tx_id,
                "claim",
                ok=True,
                minted=self.economy.relief_balance,
                detail={
                    "kind": "relief",
                    "balance": self.economy.relief_balance,
                    "expires_at": expires_at,
                },
            )

        self.state.claimed.add(sender)
        self.state.mint(sender, self.economy.starter_balance, expires_at)
        # Starter plots deliberately do *not* advance the land price curve: the
        # curve prices market demand (what buyers are willing to pay), not map
        # occupancy, so a wave of new players does not price each other out
        # before anyone has farmed a single boniato.
        granted = [
            self.state.mint_land(sender).land_id
            for _ in range(self.economy.starter_lands)
        ]
        return Receipt(
            tx.tx_id,
            "claim",
            ok=True,
            minted=self.economy.starter_balance,
            detail={
                "kind": "starter_kit",
                "balance": self.economy.starter_balance,
                "lands": granted,
                "expires_at": expires_at,
            },
        )

    def _transfer(
        self, sender: bytes, action: Transfer, _context: BlockContext
    ) -> str | None:
        if action.recipient == sender:
            return "self-transfer"
        if action.amount <= 0:
            return "amount must be positive"
        # Gas is already committed by the caller, so affordability is checked
        # against what remains after it.
        if self.state.balance_of(sender) - self.economy.gas_fee < action.amount:
            return "insufficient balance"
        return None

    def _buy_land(
        self, sender: bytes, _action: BuyLand, _context: BlockContext
    ) -> str | None:
        price = self.state.next_land_price
        if self.state.balance_of(sender) - self.economy.gas_fee < price:
            return "cannot afford land"
        return None

    def _plant(
        self, sender: bytes, action: Plant, _context: BlockContext
    ) -> str | None:
        plot = self.state.farmlands.get(action.land_id)
        if plot is None:
            return "no such plot"
        if plot.owner != sender:
            return "not the owner"
        if plot.is_planted:
            return "already planted"
        if (
            self.state.balance_of(sender) - self.economy.gas_fee
            < self.economy.seed_cost
        ):
            return "cannot afford seed"
        return None

    def _harvest(
        self, sender: bytes, action: Harvest, context: BlockContext
    ) -> str | None:
        plot = self.state.farmlands.get(action.land_id)
        if plot is None:
            return "no such plot"
        if plot.owner != sender:
            return "not the owner"
        if not plot.is_planted:
            return "nothing planted"
        if not plot.is_ready(context.height):
            return f"not ready until block {plot.ready_at}"
        return None

    def _fertilize(
        self, sender: bytes, action: Fertilize, context: BlockContext
    ) -> str | None:
        plot = self.state.farmlands.get(action.land_id)
        if plot is None:
            return "no such plot"
        if plot.owner != sender:
            return "not the owner"
        if not plot.is_planted:
            return "nothing planted"
        if plot.is_ready(context.height):
            return "already ready"
        if action.amount <= 0:
            return "amount must be positive"
        if self.state.fertilizer_of(sender) < action.amount:
            return "not enough fertilizer"
        blocks_cut, _ = self._fertilizer_effect(plot, action.amount)
        if blocks_cut == 0:
            return "would not shorten growth"
        return None

    # -- mutations ------------------------------------------------------------

    def _commit(
        self,
        tx: Transaction,
        action: Action,
        sender: bytes,
        context: BlockContext,
        gas: int,
    ) -> Receipt:
        """Apply an action whose preconditions have already been established."""
        if isinstance(action, Transfer):
            # The lots move intact, expiry dates and all. Handing the recipient
            # fresh boniatos instead would make spoilage trivially avoidable:
            # bounce a spoiling batch between two of your own keys and it never
            # ages. So a transfer passes on the age of what you actually held,
            # and the oldest goes first.
            lots = self.state.debit(sender, action.amount)
            self.state.deposit(action.recipient, lots)
            return Receipt(
                tx.tx_id,
                action.name,
                ok=True,
                gas_burned=gas,
                detail={
                    # The whole key, not a prefix. Serialized IPv8 keys share a
                    # long ASN.1 curve header, so a truncated one is identical for
                    # every account on the chain and identifies nobody. Shortening
                    # for display is the client's decision to make, not ours, and
                    # it needs the full key to resolve a name for the recipient.
                    "to": action.recipient.hex(),
                    "amount": action.amount,
                    "lots": [
                        {"amount": lot.amount, "expires_at": lot.expires_at}
                        for lot in lots
                    ],
                },
            )

        if isinstance(action, BuyLand):
            price = self.state.next_land_price
            # The payment is burned rather than paid to a treasury: it keeps the
            # land market deflationary and leaves no privileged account.
            self.state.burn(sender, price)
            plot = self.state.mint_land(sender)
            self.state.next_land_price = econ.next_land_price(price, self.economy)
            return Receipt(
                tx.tx_id,
                action.name,
                ok=True,
                gas_burned=gas,
                burned=price,
                detail={
                    "land_id": plot.land_id,
                    "price": price,
                    "fertility_bp": plot.fertility_bp,
                    "coords": econ.coords(plot.land_id, self.economy),
                    "next_price": self.state.next_land_price,
                },
            )

        if isinstance(action, Plant):
            plot = self.state.farmlands[action.land_id]
            self.state.burn(sender, self.economy.seed_cost)
            plot.planted_at = context.height
            plot.ready_at = context.height + self.economy.growth_blocks
            plot.blight_bp = 0
            return Receipt(
                tx.tx_id,
                action.name,
                ok=True,
                gas_burned=gas,
                burned=self.economy.seed_cost,
                detail={"land_id": plot.land_id, "ready_at": plot.ready_at},
            )

        if isinstance(action, Harvest):
            plot = self.state.farmlands[action.land_id]
            amount = self._yield_of(plot, tx, context)
            expires_at = self._expiry_at(context.height)
            self.state.mint(sender, amount, expires_at)
            harvested_with_blight = plot.blight_bp
            plot.clear_crop()
            return Receipt(
                tx.tx_id,
                action.name,
                ok=True,
                gas_burned=gas,
                minted=amount,
                detail={
                    "land_id": plot.land_id,
                    "amount": amount,
                    "expires_at": expires_at,
                    "fertility_bp": plot.fertility_bp,
                    "adjacent_owned": self.state.adjacent_owned_count(plot),
                    "blight_bp": harvested_with_blight,
                },
            )

        if isinstance(action, Fertilize):
            plot = self.state.farmlands[action.land_id]
            blocks_cut, consumed = self._fertilizer_effect(plot, action.amount)
            self.state.spend_fertilizer(sender, consumed)
            plot.ready_at -= blocks_cut
            return Receipt(
                tx.tx_id,
                action.name,
                ok=True,
                gas_burned=gas,
                detail={
                    "land_id": plot.land_id,
                    "blocks_cut": blocks_cut,
                    "consumed": consumed,
                    "refunded": action.amount - consumed,
                    "ready_at": plot.ready_at,
                },
            )

        raise AssertionError(f"unhandled action: {action!r}")  # pragma: no cover

    def _fertilizer_effect(self, plot: Farmland, amount: int) -> tuple[int, int]:
        """``(blocks_cut, fertilizer_consumed)`` for spending on ``plot``."""
        return econ.fertilizer_effect(
            planted_at=plot.planted_at,
            ready_at=plot.ready_at,
            amount=amount,
            economy=self.economy,
        )

    def _yield_of(self, plot: Farmland, tx: Transaction, context: BlockContext) -> int:
        """How much this harvest mints, per the economy's rules."""
        return econ.harvest_amount(
            entropy=context.entropy,
            land_id=plot.land_id,
            tx_hash=tx.tx_hash,
            fertility_bp=plot.fertility_bp,
            adjacent_owned=self.state.adjacent_owned_count(plot),
            owner_land_count=self.state.land_count_of(plot.owner),
            blight_bp=plot.blight_bp,
            economy=self.economy,
        )
