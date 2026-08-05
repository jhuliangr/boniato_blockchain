"""Consensus and execution, composed: a chain whose head has a world state.

:class:`~blockchain.consensus.chain.Chain` decides which blocks exist and which
branch wins; :class:`~blockchain.execution.StateMachine` decides what a block
means. Neither knows about the other, and something has to hold them together
and keep them consistent when the head moves. That is this class.

It is the piece both node shells are built on: the HTTP node in
:mod:`blockchain.access.node` and the P2P node in
:mod:`blockchain.network.community` differ in how blocks *arrive*, not in what
happens to them once they do.

**Reorganisation replays from genesis.** When a competing branch wins, the state
built on the old one is wrong and has to be rebuilt. Undoing a block in place
would be faster, but the execution layer has no inverse: spoilage destroys lots,
a harvest mints against a hash-derived yield, and reconstructing what a block
consumed means storing an undo log for every one of them. Replaying is
``O(height)`` in a way a full node could not accept -- Bitcoin keeps undo data
per block precisely to avoid it -- and it is *obviously* correct, which at this
scale is worth more than the speed. :meth:`rebuild_cost` reports what it costs so
the benchmark can put a number on the trade rather than an adjective.

The mempool follows the head automatically: transactions in a reverted block go
back to pending (they were never really confirmed), transactions in an applied
block leave it. A transaction that appears on both branches is therefore
removed, not duplicated, because reverting is processed before applying.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from blockchain.consensus.chain import Chain, ChainUpdate
from blockchain.consensus.validation import MAX_BLOCK_TRANSACTIONS
from blockchain.core import Block, Transaction
from blockchain.execution import DEFAULT_ECONOMY, Economy, Receipt, StateMachine, SystemEvent, WorldState

#: Leading zero bits required per block. Low enough that a block is found while
#: somebody watches, high enough that the search is real work.
DEFAULT_DIFFICULTY = 10


@dataclass(frozen=True)
class BlockOutcome:
    """What executing one block produced."""

    block: Block
    state_root: str
    receipts: tuple[Receipt, ...] = ()
    events: tuple[SystemEvent, ...] = ()


@dataclass(frozen=True)
class LedgerUpdate:
    """A :class:`ChainUpdate` plus the execution that followed from it."""

    chain: ChainUpdate
    #: Outcomes for the blocks newly on the active chain, oldest first. Empty
    #: unless the head moved.
    applied: tuple[BlockOutcome, ...] = field(default=())
    #: Transactions that went back to the mempool because their block was undone.
    returned: tuple[Transaction, ...] = ()

    @property
    def status(self) -> str:
        return self.chain.status

    @property
    def head_moved(self) -> bool:
        return self.chain.head_moved

    @property
    def reorg_depth(self) -> int:
        return self.chain.depth


class Ledger:
    """One node's chain, its world state, and the work waiting to go in."""

    def __init__(
        self,
        economy: Economy | None = None,
        difficulty: int = DEFAULT_DIFFICULTY,
        genesis_timestamp: int = 0,
    ) -> None:
        self.economy = economy if economy is not None else DEFAULT_ECONOMY
        self.chain = Chain(Block.genesis(timestamp=genesis_timestamp), difficulty)
        self.machine = StateMachine(WorldState.genesis(self.economy), self.economy)
        #: Pending transactions, insertion-ordered so block assembly is FIFO.
        self._pending: dict[bytes, Transaction] = {}
        #: State root after each applied block, so a peer can be shown where a
        #: past block landed without replaying the chain to find out.
        self._roots: dict[bytes, str] = {self.chain.head_hash: self.state_root}
        #: Blocks replayed since start, the price paid for reorganisations.
        self._replayed_blocks = 0
        self._reorgs = 0

    # -- reads ----------------------------------------------------------------

    @property
    def difficulty(self) -> int:
        return self.chain.difficulty

    @property
    def state(self) -> WorldState:
        return self.machine.state

    @property
    def state_root(self) -> str:
        return self.machine.state.state_root

    @property
    def head(self) -> Block:
        return self.chain.head

    @property
    def height(self) -> int:
        return self.chain.height

    @property
    def pending(self) -> tuple[Transaction, ...]:
        return tuple(self._pending.values())

    def __len__(self) -> int:
        return len(self._pending)

    def state_root_of(self, block_hash: bytes) -> str | None:
        return self._roots.get(block_hash)

    def active_chain(self) -> list[Block]:
        return self.chain.active_chain()

    def rebuild_cost(self) -> dict:
        """Blocks re-executed because of reorganisations, and how many there were."""
        return {"reorgs": self._reorgs, "blocks_replayed": self._replayed_blocks}

    # -- submitting work ------------------------------------------------------

    def submit(self, transaction: Transaction) -> bool:
        """Queue a signed transaction. ``False`` if it is bad or already known.

        Only the stateless half of validation happens here. Whether the sender
        owns the plot or can afford the seed depends on the state at execution
        time, which is a different block from this one, so the mempool has no
        business deciding it -- and a transaction that turns out to fail still
        gets mined, and still pays gas.
        """
        if not transaction.is_valid():
            return False
        if transaction.tx_hash in self._pending:
            return False
        self._pending[transaction.tx_hash] = transaction
        return True

    def candidate(self, timestamp: int | None = None, limit: int = MAX_BLOCK_TRANSACTIONS) -> Block:
        """Build the next block on top of the head, unmined.

        An *empty* candidate is legitimate and necessary rather than a waste:
        height is the clock, so a block with nothing in it is still what makes
        crops grow and boniatos age.
        """
        transactions = tuple(self._pending.values())[:limit]
        return Block.create(
            index=self.height + 1,
            prev_hash=self.chain.head_hash,
            transactions=transactions,
            timestamp=int(time.time()) if timestamp is None else timestamp,
        )

    # -- connecting blocks ----------------------------------------------------

    def connect(self, block: Block) -> LedgerUpdate:
        """Offer a block to the chain and execute whatever that decides.

        This is the single path a block takes into a node, whether it was mined
        here or arrived from a peer. Nothing else is allowed to touch the state.
        """
        update = self.chain.add(block)
        if not update.head_moved:
            return LedgerUpdate(update)

        returned: tuple[Transaction, ...] = ()
        if update.reverted:
            returned = self._revert(update.reverted)

        outcomes = self._advance(update)
        return LedgerUpdate(update, applied=outcomes, returned=returned)

    def _revert(self, reverted: tuple[Block, ...]) -> tuple[Transaction, ...]:
        """Undo blocks by rebuilding the state, and un-confirm their transactions."""
        self._reorgs += 1
        returned: list[Transaction] = []
        for block in reverted:
            for tx in block.transactions:
                if tx.tx_hash not in self._pending:
                    self._pending[tx.tx_hash] = tx
                    returned.append(tx)
        return tuple(returned)

    def _advance(self, update: ChainUpdate) -> tuple[BlockOutcome, ...]:
        """Bring the state to the new head and report what the new blocks did."""
        if update.reverted:
            # The state belongs to a branch that lost. Only a full replay can
            # produce the one that belongs to the branch that won.
            return self._replay(update.applied)
        return tuple(self._execute(block) for block in update.applied)

    def _replay(self, applied: tuple[Block, ...]) -> tuple[BlockOutcome, ...]:
        """Rebuild the world from genesis along the branch that now wins."""
        self.machine = StateMachine(WorldState.genesis(self.economy), self.economy)
        self._roots = {self.chain.genesis.block_hash: self.state_root}
        outcomes: list[BlockOutcome] = []
        new_blocks = set(b.block_hash for b in applied)
        for block in self.chain.active_chain()[1:]:  # genesis carries no transactions
            self._replayed_blocks += 1
            outcome = self._execute(block)
            if outcome.block.block_hash in new_blocks:
                outcomes.append(outcome)
        return tuple(outcomes)

    def _execute(self, block: Block) -> BlockOutcome:
        """Apply one block to the state and take its transactions off the queue."""
        receipts, events = self.machine.apply_block(block)
        for tx in block.transactions:
            self._pending.pop(tx.tx_hash, None)
        root = self.state_root
        self._roots[block.block_hash] = root
        return BlockOutcome(block, root, tuple(receipts), tuple(events))

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"Ledger(height={self.height}, pending={len(self._pending)}, "
            f"state_root={self.state_root[:12]}…)"
        )
