"""A single node with a chain, a mempool and a keyring.

Everything the HTTP layer needs, with no HTTP in it. :mod:`blockchain.access.routes`
turns requests into calls on this class and its results into JSON; keeping the two
apart means the whole API can be tested without opening a socket.

What this node is **not**: it has no peers. It runs the same
:class:`~blockchain.consensus.Ledger` as a networked peer -- same fork choice,
same validation, same replay protection -- but nothing ever offers it a
competing block, so the machinery never has anything to decide. Mining here is a
local, synchronous act that drains the mempool into the next block. The peer
that does have neighbours is :class:`~blockchain.network.BlockchainCommunity`,
and ``scripts/run_chain.py`` runs a fleet of them.

That both shells sit on one ledger is the point: an HTTP request and a UDP
packet are two ways of arriving at the same state transition, and only one
implementation of it exists.

The keyring is the other deliberate simplification, and the more visible one: the
node holds private keys and signs on a client's behalf, because a browser cannot
reach IPv8's elliptic-curve primitives. That makes this a **custodial wallet**,
suitable for a demo and nothing else. A real DApp signs in the browser and hands
the node an already-signed transaction, which this class would accept just as
happily through :meth:`accept`.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from blockchain.consensus import Ledger
from blockchain.core import Block, Transaction, has_proof_of_work, mine
from blockchain.crypto import Identity
from blockchain.execution import (
    Action,
    BlightEvent,
    Economy,
    Receipt,
    RotEvent,
    StateMachine,
    SystemEvent,
    WorldState,
    signed,
)

#: Modest by design: blocks are mined on demand while somebody watches a browser,
#: so the work has to be visible in the log without being a wait.
DEFAULT_DIFFICULTY = 10

#: How many activity entries to keep. The feed is observability, not consensus
#: data, so it is bounded rather than complete.
ACTIVITY_LIMIT = 500

#: Hex characters of key tail used to name an account the node holds no label for.
_FINGERPRINT_CHARS = 12


@dataclass
class Wallet:
    """A demo identity the node signs for."""

    identity: Identity
    label: str

    @property
    def public_key(self) -> bytes:
        return self.identity.public_key


@dataclass
class MinedBlock:
    """A block paired with the state root it produced.

    ``state_root`` travels beside the block rather than inside it, because the
    header does not commit to one. Nodes therefore *agree* on state without
    *proving* it to each other: two peers can compare roots and detect
    divergence, but neither can hand a third party a proof that a given block
    yields a given state. Committing the root in the header is what closes that
    gap, and it is the natural next change to the block format.
    """

    block: Block
    state_root: str


class FarmNode:
    """One node's view of the chain, its pending work and its wallets.

    Every mutating method takes a lock. The HTTP server is threaded, so a mine
    triggered by one request can otherwise interleave with a submission from
    another, and the mempool would lose transactions.
    """

    def __init__(
        self,
        economy: Economy | None = None,
        difficulty: int = DEFAULT_DIFFICULTY,
    ) -> None:
        self.economy = economy if economy is not None else Economy()
        self.ledger = Ledger(economy=self.economy, difficulty=difficulty)
        self.wallets: dict[bytes, Wallet] = {}
        self.activity: list[dict] = []
        self._lock = threading.Lock()

    # -- reads ----------------------------------------------------------------

    @property
    def difficulty(self) -> int:
        return self.ledger.difficulty

    @property
    def machine(self) -> StateMachine:
        return self.ledger.machine

    @property
    def state(self) -> WorldState:
        return self.ledger.state

    @property
    def height(self) -> int:
        return self.ledger.height

    @property
    def mempool(self) -> tuple[Transaction, ...]:
        """Transactions waiting for a block. Read-only: submit through :meth:`accept`."""
        return self.ledger.pending

    @property
    def blocks(self) -> list[MinedBlock]:
        """The active chain, genesis first, each block with the state it produced."""
        return [
            MinedBlock(block, self.ledger.state_root_of(block.block_hash) or "")
            for block in self.ledger.active_chain()
        ]

    @property
    def head(self) -> MinedBlock:
        return MinedBlock(self.ledger.head, self.ledger.state_root)

    def wallet_of(self, public_key: bytes) -> Wallet | None:
        return self.wallets.get(public_key)

    def label_of(self, public_key: bytes) -> str:
        """A wallet's label, or a fingerprint for a key we do not hold.

        The fingerprint is the **tail** of the key. Serialized IPv8 public keys
        begin with a long common ASN.1 curve header, so a leading slice is
        byte-identical for every account on the chain and names nobody.
        """
        wallet = self.wallets.get(public_key)
        if wallet is not None:
            return wallet.label
        return public_key.hex()[-_FINGERPRINT_CHARS:] if public_key else ""

    def recent_blocks(self, limit: int) -> list[MinedBlock]:
        """Newest first."""
        return list(reversed(self.blocks[-limit:])) if limit > 0 else []

    def recent_activity(self, limit: int) -> list[dict]:
        """Newest first."""
        return list(reversed(self.activity[-limit:])) if limit > 0 else []

    # -- wallets --------------------------------------------------------------

    def create_wallet(self, label: str | None = None) -> Wallet:
        with self._lock:
            identity = Identity.generate()
            name = label or f"farmer-{len(self.wallets) + 1}"
            wallet = Wallet(identity=identity, label=name)
            self.wallets[wallet.public_key] = wallet
            return wallet

    # -- submitting work ------------------------------------------------------

    def submit(self, public_key: bytes, action: Action) -> Transaction:
        """Sign ``action`` with a held key and queue it. Custodial, demo-only."""
        wallet = self.wallets.get(public_key)
        if wallet is None:
            raise KeyError("unknown wallet")
        return self.accept(signed(wallet.identity, action))

    def accept(self, transaction: Transaction) -> Transaction:
        """Queue an already-signed transaction, rejecting a bad signature.

        The path a non-custodial client would use. Validation here is only the
        cheap, stateless part: preconditions like ownership and funds depend on
        the state at execution time, so they are the engine's business, not the
        mempool's.
        """
        with self._lock:
            if not self.ledger.submit(transaction):
                # The ledger refuses a bad signature and a transaction it already
                # holds. Only the first is the caller's mistake; re-submitting a
                # queued transaction is idempotent, not an error.
                if not transaction.is_valid():
                    raise ValueError("invalid signature")
            return transaction

    # -- mining ---------------------------------------------------------------

    def mine_next(self) -> tuple[MinedBlock, list[dict], list[dict]]:
        """Mine every pending transaction into the next block and execute it.

        Returns the block plus its outcomes already shaped for the feed, as
        ``(mined, receipt_entries, event_entries)``, so callers never have to
        format a receipt themselves or fish this block's entries back out of the
        log.

        Mining an *empty* block is a legitimate and necessary operation, not a
        no-op: height is the clock, so a block with nothing in it is still what
        makes crops grow and boniatos age.
        """
        with self._lock:
            block = mine(self.ledger.candidate(timestamp=int(time.time())), self.difficulty)
            assert has_proof_of_work(block, self.difficulty)

            # Through the ledger, not around it: the block this node just mined
            # is validated exactly like one from a peer would be. If our own
            # block is unacceptable, we want to hear it here.
            update = self.ledger.connect(block)
            if not update.applied:  # pragma: no cover - would be a bug in mining
                raise AssertionError(f"the node rejected its own block: {update.chain.reason}")

            outcome = update.applied[-1]
            mined = MinedBlock(outcome.block, outcome.state_root)
            return (mined, *self._record(block.index, list(outcome.receipts), list(outcome.events)))

    # -- activity log ---------------------------------------------------------

    def _record(
        self, height: int, receipts: list[Receipt], events: list[SystemEvent]
    ) -> tuple[list[dict], list[dict]]:
        """Append this block's outcomes to the feed, system events first.

        Same order the engine applied them in, so the feed reads as a truthful
        account of the block rather than a reordering of it.
        """
        event_entries = [self._event_entry(height, event) for event in events]
        receipt_entries = [
            {
                "height": height,
                "tx_id": receipt.tx_id,
                "action": receipt.action,
                "ok": receipt.ok,
                "reason": receipt.reason,
                "gas_burned": receipt.gas_burned,
                "minted": receipt.minted,
                "burned": receipt.burned,
                "detail": receipt.detail,
                # Who did this. A feed the client cannot attribute is a feed it
                # cannot filter by wallet, which is the first thing a player wants.
                "public_key": receipt.public_key.hex(),
                "label": self.label_of(receipt.public_key),
            }
            for receipt in receipts
        ]
        self.activity.extend(event_entries)
        self.activity.extend(receipt_entries)
        # Bound the log rather than let a long-running demo grow without limit.
        if len(self.activity) > ACTIVITY_LIMIT:
            del self.activity[: len(self.activity) - ACTIVITY_LIMIT]
        return receipt_entries, event_entries

    def _event_entry(self, height: int, event: SystemEvent) -> dict:
        base = {
            "height": height,
            "tx_id": "",
            "action": event.name,
            "ok": True,
            "reason": "",
            "gas_burned": 0,
            "minted": 0,
            "burned": 0,
            # Nobody signed a system event, so there is no signer. Spoilage still
            # happens *to* an account, and a rot entry names it below; a blight
            # belongs to a plot, whose owner the client reads off the map.
            "public_key": "",
            "label": "",
        }
        if isinstance(event, RotEvent):
            return {
                **base,
                "public_key": event.public_key.hex(),
                "label": self.label_of(event.public_key),
                "detail": {
                    "public_key": event.public_key.hex(),
                    "label": self.label_of(event.public_key),
                    "rotted": event.rotted,
                    "fertilizer": event.fertilizer,
                },
            }
        if isinstance(event, BlightEvent):
            return {
                **base,
                "detail": {"land_id": event.land_id, "penalty_bp": event.penalty_bp},
            }
        raise AssertionError(f"unhandled system event: {event!r}")  # pragma: no cover
