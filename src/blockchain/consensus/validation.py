"""What makes a block acceptable, independently of which branch it lands on.

Split out from :mod:`blockchain.consensus.chain` because these are the rules a
peer can check the moment a block arrives, with nothing but the block and its
parent in hand. The rules that need the whole branch -- transaction replay,
fork choice -- live in the chain, which is the only thing that knows the tree.

Every check returns a **reason string** rather than raising or returning a bare
``False``. A node that drops a block silently is a node nobody can debug, and
the reason is what the demo prints when it rejects a forged block.

Ordering is deliberate: the cheap structural checks run before the expensive
cryptography, so a peer flooding malformed blocks costs us far less than it
costs them.
"""

from __future__ import annotations

from blockchain.core.block import Block
from blockchain.core.pow import has_proof_of_work

#: Transactions per block. Not an economic parameter -- a wire one: a block is
#: gossiped as a single UDP datagram carrying its transactions in full, and each
#: signed transaction is ~256 bytes (a 409-bit public key and its signature).
#: Thirty-two keeps a full block near 8 KB, which survives loopback and local
#: networks intact. Lifting it properly means not shipping the bodies at all:
#: announce the header plus transaction ids and let the peer reconstruct the
#: block from its own mempool, which is what Bitcoin's compact blocks do
#: (BIP 152). That is a natural next step, not a rewrite -- the mempool this
#: project already gossips is the piece such a scheme needs.
MAX_BLOCK_TRANSACTIONS = 32


def check_block(block: Block, *, difficulty: int, parent: Block | None = None) -> str | None:
    """Return why ``block`` is unacceptable, or ``None`` if it is fine.

    With ``parent`` supplied the link to it is checked too. Without it, only the
    self-contained rules run, which is all a peer can do for a block whose
    ancestor it has not seen yet.
    """
    if block.index < 0:
        return "negative block index"

    if len(block.transactions) > MAX_BLOCK_TRANSACTIONS:
        return f"too many transactions ({len(block.transactions)} > {MAX_BLOCK_TRANSACTIONS})"

    # A block that carries the same transaction twice would execute it twice.
    # Cheaper to catch here than to reason about downstream.
    tx_hashes = {tx.tx_hash for tx in block.transactions}
    if len(tx_hashes) != len(block.transactions):
        return "duplicate transaction inside the block"

    # The Merkle root is the header's only commitment to the bodies, so this is
    # what stops a miner swapping the transactions after finding a nonce.
    if not block.has_consistent_merkle_root():
        return "merkle root does not commit to these transactions"

    # Signatures last among the self-contained checks: this is the expensive one.
    for tx in block.transactions:
        if not tx.is_valid():
            return f"invalid signature on transaction {tx.tx_id[:12]}"

    if not has_proof_of_work(block, difficulty):
        return f"insufficient proof-of-work (needs {difficulty} leading zero bits)"

    if parent is not None:
        if block.prev_hash != parent.block_hash:
            return "prev_hash does not match the parent it was offered for"
        if block.index != parent.index + 1:
            return f"height {block.index} does not follow parent {parent.index}"
        # The only constraint worth putting on a miner-supplied timestamp. The
        # execution layer deliberately never reads it -- block height is the
        # chain's clock -- so a stricter rule would buy nothing and would reject
        # honest blocks whenever two nodes' clocks disagree.
        if block.timestamp < parent.timestamp:
            return "timestamp goes backwards"

    return None
