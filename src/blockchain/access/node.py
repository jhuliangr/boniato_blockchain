"""A single node with a chain, a mempool and a keyring.

Everything the HTTP layer needs, with no HTTP in it. :mod:`blockchain.access.routes`
turns requests into calls on this class and its results into JSON; keeping the two
apart means the whole API can be tested without opening a socket.

What this node is **not**: it has no peers. There is exactly one chain, so there
is no fork choice, no block propagation and no reorg. That work belongs to the
consensus layer and is still pending; the gossip network lives separately in
``scripts/run_network.py``. Here, mining is a local, synchronous act that drains
the mempool into the next block.

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
    """A block plus the node's bookkeeping about it.

    ``state_root`` is recorded here rather than read off the header, because the
    header does not carry one yet: committing the state root inside the block is
    the next piece of consensus work. Until it does, this is one node's note of
    where its state landed, which is enough to show divergence in a demo but not
    enough to *prove* agreement to a peer.
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
        self.difficulty = difficulty
        self.machine = StateMachine(WorldState.genesis(self.economy), self.economy)
        self.blocks: list[MinedBlock] = [
            MinedBlock(Block.genesis(timestamp=0), self.machine.state.state_root)
        ]
        self.mempool: list[Transaction] = []
        self.wallets: dict[bytes, Wallet] = {}
        self.activity: list[dict] = []
        self._lock = threading.Lock()

    # -- reads ----------------------------------------------------------------

    @property
    def state(self) -> WorldState:
        return self.machine.state

    @property
    def height(self) -> int:
        return self.blocks[-1].block.index

    @property
    def head(self) -> MinedBlock:
        return self.blocks[-1]

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
        if not transaction.is_valid():
            raise ValueError("invalid signature")
        with self._lock:
            self.mempool.append(transaction)
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
            transactions = tuple(self.mempool)
            self.mempool.clear()

            candidate = Block.create(
                index=self.height + 1,
                prev_hash=self.head.block.block_hash,
                transactions=transactions,
                timestamp=int(time.time()),
            )
            block = mine(candidate, self.difficulty)
            assert has_proof_of_work(block, self.difficulty)

            receipts, events = self.machine.apply_block(block)
            mined = MinedBlock(block, self.machine.state.state_root)
            self.blocks.append(mined)
            return (mined, *self._record(block.index, receipts, events))

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
