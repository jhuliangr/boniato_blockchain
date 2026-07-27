"""The :class:`Block`: a group of transactions committed as one unit.

Week-1 goal ("blocks, including grouping transactions using a Merkle tree"):
a block bundles a batch of accepted transactions, summarises them with a single
**Merkle root**, and chains to its predecessor via ``prev_hash``. Proof-of-Work
lives next door in :mod:`blockchain.core.pow`; a block only *carries* the
``nonce`` a miner searched for it does not know how to mine itself.

Design decisions (consistent with :class:`Transaction`):

- The block is an **immutable** value object (``frozen`` dataclass). Mining
  produces a *new* block via :meth:`with_nonce` rather than mutating in place,
  which keeps :attr:`block_hash` a pure function of the contents.
- The **header** (everything except the transaction bodies) is what gets
  hashed. The Merkle root is the header's single commitment to the whole
  transaction set, so a peer can validate the PoW over a small fixed-size
  header and check inclusion separately with a Merkle proof.
- Domain separation: the header digest is tagged so a block hash can never
  collide with a transaction or Merkle-node hash.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from blockchain.core.merkle import MerkleTree
from blockchain.core.transaction import Transaction

# Tag mixed into the header digest so a block hash is unmistakable for any other
# hash in the system (transactions use ``harbourspace-tx-v1``).
_DOMAIN_TAG = b"harbourspace-block-v1"

# The genesis block points at the all-zero hash there is no predecessor.
GENESIS_PREV_HASH = b"\x00" * 32

# Widths used when serialising header integers, big-endian.
_INDEX_BYTES = 8
_TIMESTAMP_BYTES = 8
_NONCE_BYTES = 8


def merkle_root_of(transactions: tuple[Transaction, ...]) -> bytes:
    """Merkle root over the transactions' hashes, in the given order."""
    return MerkleTree([tx.tx_hash for tx in transactions]).root


@dataclass(frozen=True)
class Block:
    """An immutable, Merkle-committed batch of transactions."""

    index: int
    prev_hash: bytes
    merkle_root: bytes
    timestamp: int
    nonce: int
    transactions: tuple[Transaction, ...] = field(default_factory=tuple)

    # -- construction ---------------------------------------------------------

    @classmethod
    def create(
        cls,
        index: int,
        prev_hash: bytes,
        transactions: tuple[Transaction, ...] | list[Transaction],
        timestamp: int,
        nonce: int = 0,
    ) -> "Block":
        """Build a block, computing its Merkle root from ``transactions``.

        The root is derived here rather than taken as input, so a block's
        :attr:`merkle_root` always matches the transactions it carries.
        """
        txs = tuple(transactions)
        return cls(
            index=index,
            prev_hash=prev_hash,
            merkle_root=merkle_root_of(txs),
            timestamp=timestamp,
            nonce=nonce,
            transactions=txs,
        )

    @classmethod
    def genesis(cls, timestamp: int = 0) -> "Block":
        """The well-known first block: height 0, no predecessor, no txs."""
        return cls.create(0, GENESIS_PREV_HASH, (), timestamp=timestamp, nonce=0)

    def with_nonce(self, nonce: int) -> "Block":
        """Return a copy of this block carrying a different ``nonce``.

        This is the one operation Proof-of-Work needs: try a nonce, hash, repeat.
        Everything else about the block is fixed.
        """
        return Block(
            index=self.index,
            prev_hash=self.prev_hash,
            merkle_root=self.merkle_root,
            timestamp=self.timestamp,
            nonce=nonce,
            transactions=self.transactions,
        )

    # -- identity / hashing ---------------------------------------------------

    @property
    def header_bytes(self) -> bytes:
        """The fixed-size header a miner hashes (excludes transaction bodies)."""
        return (
            _DOMAIN_TAG
            + self.index.to_bytes(_INDEX_BYTES, "big")
            + self.prev_hash
            + self.merkle_root
            + self.timestamp.to_bytes(_TIMESTAMP_BYTES, "big")
            + self.nonce.to_bytes(_NONCE_BYTES, "big")
        )

    @property
    def block_hash(self) -> bytes:
        """SHA-256 of the header the block's unique identifier."""
        return hashlib.sha256(self.header_bytes).digest()

    @property
    def block_id(self) -> str:
        """Hex form of :attr:`block_hash`, convenient for logs and chaining."""
        return self.block_hash.hex()

    # -- validation -----------------------------------------------------------

    def has_consistent_merkle_root(self) -> bool:
        """``True`` iff :attr:`merkle_root` matches :attr:`transactions`."""
        return self.merkle_root == merkle_root_of(self.transactions)

    def is_valid(self) -> bool:
        """Structural self-check: Merkle root is honest and every tx verifies.

        Proof-of-Work is *not* checked here it depends on the agreed difficulty,
        which is a chain-level policy (see :func:`blockchain.core.pow.has_proof_of_work`).
        """
        if not self.has_consistent_merkle_root():
            return False
        return all(tx.is_valid() for tx in self.transactions)

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"Block(index={self.index}, txs={len(self.transactions)}, "
            f"hash={self.block_id[:12]}…)"
        )
