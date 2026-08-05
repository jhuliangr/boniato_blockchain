"""The block tree and the rule that picks one branch of it as *the* chain.

This is the consensus layer of the stack (infrastructure -> **consensus** ->
execution -> access -> application). Everything below it produces blocks;
everything above it needs exactly one ordered history. Reconciling those two
facts is this module's whole job.

A node does not hold *a chain*; it holds a **tree**. Two miners who solve a
block at the same height both broadcast a valid block, and every peer that sees
both must keep both and decide. :class:`Chain` therefore stores every valid
block it has ever connected, keyed by hash, and derives the active chain from
the tree by **cumulative work** rather than storing it.

Three rules do the work:

**Fork choice: heaviest chain, not longest.** Each block contributes
``2**difficulty`` units of work, so a branch's weight is the total expected
number of hashes that went into it. With the fixed difficulty this project
uses, the heaviest branch is always the longest one, so the two rules coincide
today. They stop coinciding the moment difficulty retargets, and picking the
longest chain then lets an attacker win with a long branch of cheap blocks.
Expressing the rule as work now costs nothing and is the rule that is actually
correct. (Nakamoto, *Bitcoin*, 2008, §5; Bitcoin Core switched from
``nHeight`` to ``nChainWork`` for exactly this reason.)

**Ties go to the incumbent.** A block that merely equals the head's work does
not replace it: first seen wins. Without that, two peers with the same two
blocks could flap between them forever, and neither would ever confirm.

**No transaction may be replayed.** A signed transaction is a bearer
instrument: nothing inside it names the block it belongs to, so a miner who
copies one out of an old block could execute it a second time. A block is
therefore invalid if it carries a transaction that already appears in one of
its **ancestors** -- ancestors, not "the chain", because a transaction spent on
the branch we happen to be following is legitimately unspent on a competing
one. See :meth:`Chain._replays_transaction` for the cost of enforcing this.

Blocks that arrive before their parent (out-of-order delivery, or a peer that
is further ahead than we are) are **parked as orphans** and connected
automatically once the missing ancestor turns up.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from blockchain.consensus.validation import check_block
from blockchain.core.block import Block

#: Cap on parked blocks. An orphan pool is attacker-controlled memory: anyone
#: can send blocks that claim an unknown parent. Dropping the oldest is enough,
#: because a genuinely missing ancestor is re-requested and will come back.
MAX_ORPHANS = 256


def work_of(difficulty: int) -> int:
    """Expected hashes behind one block at ``difficulty`` leading zero bits.

    Bitcoin computes ``2**256 / (target + 1)``; with a difficulty expressed
    directly in zero bits that reduces to ``2**difficulty``.
    """
    return 1 << difficulty


# -- outcomes -----------------------------------------------------------------

#: A block we already had. Nothing changed.
DUPLICATE = "duplicate"
#: Structurally invalid, bad Proof-of-Work, or replaying a spent transaction.
INVALID = "invalid"
#: Parent unknown: parked, and the caller should go and fetch ``block.prev_hash``.
ORPHAN = "orphan"
#: Connected, but on a branch that is not the best one. Kept, not applied.
SIDE = "side"
#: Connected on top of the previous head. The common case.
EXTENDED = "extended"
#: Connected on another branch that is now heavier: the head moved sideways.
REORG = "reorg"


@dataclass(frozen=True)
class ChainEntry:
    """One connected block plus what the tree knows about its position."""

    block: Block
    height: int
    total_work: int

    @property
    def block_hash(self) -> bytes:
        return self.block.block_hash


@dataclass(frozen=True)
class ChainUpdate:
    """What :meth:`Chain.add` did, in terms the layers above can act on.

    ``reverted`` and ``applied`` describe how the *head* moved, oldest first,
    which is exactly what the execution layer needs to undo and redo, and what
    the mempool needs to put transactions back and take them out again. Both
    are empty unless the head actually moved.
    """

    status: str
    block: Block
    reverted: tuple[Block, ...] = ()
    applied: tuple[Block, ...] = ()
    reason: str = ""

    @property
    def head_moved(self) -> bool:
        return self.status in (EXTENDED, REORG)

    @property
    def accepted(self) -> bool:
        """Did the tree keep this block at all?"""
        return self.status in (EXTENDED, REORG, SIDE)

    @property
    def depth(self) -> int:
        """How many blocks a reorg undid. ``0`` for an ordinary extension."""
        return len(self.reverted)


class Chain:
    """A tree of valid blocks, and the heaviest branch through it."""

    def __init__(self, genesis: Block, difficulty: int) -> None:
        self.difficulty = difficulty
        self._entries: dict[bytes, ChainEntry] = {}
        #: Parked blocks, grouped by the parent they are waiting for.
        self._orphans: dict[bytes, list[Block]] = {}
        self._orphan_count = 0

        genesis_entry = ChainEntry(genesis, height=0, total_work=work_of(difficulty))
        self._entries[genesis_entry.block_hash] = genesis_entry
        self._genesis_hash = genesis_entry.block_hash
        self._head_hash = genesis_entry.block_hash

    # -- reads ----------------------------------------------------------------

    @property
    def head(self) -> Block:
        return self._entries[self._head_hash].block

    @property
    def head_hash(self) -> bytes:
        return self._head_hash

    @property
    def height(self) -> int:
        return self._entries[self._head_hash].height

    @property
    def total_work(self) -> int:
        return self._entries[self._head_hash].total_work

    @property
    def genesis(self) -> Block:
        return self._entries[self._genesis_hash].block

    def __len__(self) -> int:
        """Number of blocks *known*, across every branch."""
        return len(self._entries)

    def __contains__(self, block_hash: bytes) -> bool:
        return block_hash in self._entries

    def get(self, block_hash: bytes) -> Block | None:
        entry = self._entries.get(block_hash)
        return entry.block if entry else None

    def entry(self, block_hash: bytes) -> ChainEntry | None:
        return self._entries.get(block_hash)

    def active_chain(self) -> list[Block]:
        """Genesis first, head last: the branch this node currently believes."""
        return [entry.block for entry in reversed(list(self._ancestry(self._head_hash)))]

    def is_active(self, block_hash: bytes) -> bool:
        """Is this block on the branch we currently follow?"""
        entry = self._entries.get(block_hash)
        if entry is None:
            return False
        walker = self._head_hash
        while walker is not None:
            if walker == block_hash:
                return True
            current = self._entries[walker]
            if current.height <= entry.height:
                return False
            walker = current.block.prev_hash if current.height else None
        return False

    @property
    def orphan_count(self) -> int:
        return self._orphan_count

    def branch_count(self) -> int:
        """How many leaves the tree has: 1 means no fork is currently known."""
        parents = {entry.block.prev_hash for entry in self._entries.values()}
        return sum(1 for h in self._entries if h not in parents)

    # -- writes ---------------------------------------------------------------

    def add(self, block: Block) -> ChainUpdate:
        """Validate ``block`` and connect it, resolving any orphans it unlocks.

        Returns one :class:`ChainUpdate` describing the *net* effect, including
        blocks that were parked and became connectable because of this one. A
        node that receives block ``n`` after ``n+1`` therefore ends up with both,
        from a single call.
        """
        block_hash = block.block_hash
        if block_hash in self._entries:
            return ChainUpdate(DUPLICATE, block)

        parent = self._entries.get(block.prev_hash)
        if parent is None:
            self._park(block)
            return ChainUpdate(ORPHAN, block, reason="parent unknown")

        reason = self._check(block, parent)
        if reason is not None:
            return ChainUpdate(INVALID, block, reason=reason)

        previous_head = self._head_hash
        self._connect(block, parent)
        self._drain_orphans(block_hash)
        return self._settle(block, previous_head)

    def _connect(self, block: Block, parent: ChainEntry) -> None:
        """Insert a validated block and let it compete for the head."""
        entry = ChainEntry(
            block,
            height=parent.height + 1,
            total_work=parent.total_work + work_of(self.difficulty),
        )
        self._entries[entry.block_hash] = entry
        # Strictly greater: an equal-work branch never displaces the incumbent.
        if entry.total_work > self._entries[self._head_hash].total_work:
            self._head_hash = entry.block_hash

    def _drain_orphans(self, connected_hash: bytes) -> None:
        """Connect every parked descendant of ``connected_hash``, breadth-first."""
        pending = [connected_hash]
        while pending:
            parent_hash = pending.pop()
            for orphan in self._take_orphans(parent_hash):
                parent = self._entries[parent_hash]
                if self._check(orphan, parent) is not None:
                    continue  # a bad block unblocks nothing; its children rot too
                self._connect(orphan, parent)
                pending.append(orphan.block_hash)

    def _settle(self, block: Block, previous_head: bytes) -> ChainUpdate:
        """Describe how the head moved, now that everything connectable is in."""
        if self._head_hash == previous_head:
            return ChainUpdate(SIDE, block)
        reverted, applied = self._path_between(previous_head, self._head_hash)
        status = EXTENDED if not reverted else REORG
        return ChainUpdate(status, block, reverted=reverted, applied=applied)

    # -- validation -----------------------------------------------------------

    def _check(self, block: Block, parent: ChainEntry) -> str | None:
        """All consensus rules for ``block`` given its parent. ``None`` == valid."""
        reason = check_block(block, difficulty=self.difficulty, parent=parent.block)
        if reason is not None:
            return reason
        if self._replays_transaction(block, parent):
            return "replays a transaction from an ancestor block"
        return None

    def _replays_transaction(self, block: Block, parent: ChainEntry) -> bool:
        """Does ``block`` re-include a transaction already in its ancestry?

        Walks the branch back to genesis, which is ``O(height)`` per block. That
        is affordable at the scale this project runs at and it is obviously
        correct, which is the trade this code chooses. Production chains do not
        pay it: Bitcoin's UTXO set makes a spent output disappear, and Ethereum's
        per-account nonce makes a replayed transaction unorderable. Both replace
        the walk with an ``O(1)`` lookup against a state they already maintain.
        """
        if not block.transactions:
            return False
        incoming = {tx.tx_hash for tx in block.transactions}
        for entry in self._ancestry(parent.block_hash):
            for tx in entry.block.transactions:
                if tx.tx_hash in incoming:
                    return True
        return False

    # -- orphan pool ----------------------------------------------------------

    def _park(self, block: Block) -> None:
        waiting = self._orphans.setdefault(block.prev_hash, [])
        if any(parked.block_hash == block.block_hash for parked in waiting):
            return
        waiting.append(block)
        self._orphan_count += 1
        self._evict_orphans()

    def _take_orphans(self, parent_hash: bytes) -> list[Block]:
        waiting = self._orphans.pop(parent_hash, [])
        self._orphan_count -= len(waiting)
        return waiting

    def _evict_orphans(self) -> None:
        """Drop the oldest parked block while the pool is over its cap."""
        while self._orphan_count > MAX_ORPHANS:
            oldest_key = next(iter(self._orphans))
            waiting = self._orphans[oldest_key]
            waiting.pop(0)
            self._orphan_count -= 1
            if not waiting:
                del self._orphans[oldest_key]

    # -- tree walking ---------------------------------------------------------

    def _ancestry(self, block_hash: bytes):
        """Yield entries from ``block_hash`` back to genesis, newest first."""
        current = self._entries.get(block_hash)
        while current is not None:
            yield current
            if current.height == 0:
                return
            current = self._entries.get(current.block.prev_hash)

    def _path_between(
        self, old_head: bytes, new_head: bytes
    ) -> tuple[tuple[Block, ...], tuple[Block, ...]]:
        """Blocks to undo and blocks to apply to move the head, oldest first.

        Walks both branches back to their common ancestor. On an ordinary
        extension the old head *is* the ancestor, so nothing is reverted.
        """
        old_branch: list[Block] = []
        new_branch: list[Block] = []
        left, right = self._entries[old_head], self._entries[new_head]

        while left.height > right.height:
            old_branch.append(left.block)
            left = self._entries[left.block.prev_hash]
        while right.height > left.height:
            new_branch.append(right.block)
            right = self._entries[right.block.prev_hash]
        while left.block_hash != right.block_hash:
            old_branch.append(left.block)
            new_branch.append(right.block)
            left = self._entries[left.block.prev_hash]
            right = self._entries[right.block.prev_hash]

        return tuple(reversed(old_branch)), tuple(reversed(new_branch))

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"Chain(height={self.height}, known={len(self._entries)}, "
            f"branches={self.branch_count()}, orphans={self._orphan_count})"
        )
