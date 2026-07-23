"""The mempool: the set of accepted, validated transactions.

Responsibilities (and *only* these):

- Keep exactly one copy of each accepted transaction (dedup by ``tx_hash``).
- Maintain a Merkle tree over the accepted transactions, in deterministic
  insertion order, so the current :attr:`root` summarises the whole set.
- Answer "do I already have this?" cheaply the pull/gossip layer needs it.

It does **not** validate signatures itself; validation is the transaction's own
responsibility (:meth:`Transaction.is_valid`) and the network layer enforces it
before calling :meth:`add`. Keeping that boundary crisp is deliberate: storage
stays a dumb, fast structure.
"""

from __future__ import annotations

from typing import Iterator

from blockchain.core import MerkleTree, Transaction


class Mempool:
    """In-memory, Merkle-backed store of accepted transactions."""

    def __init__(self) -> None:
        # Insertion-ordered mapping tx_hash -> Transaction (dicts preserve order
        # in CPython 3.7+), which keeps the Merkle leaf order deterministic.
        self._transactions: dict[bytes, Transaction] = {}
        self._tree = MerkleTree()

    # -- writes ---------------------------------------------------------------

    def add(self, transaction: Transaction) -> bool:
        """Add an already-validated transaction.

        Returns ``True`` if it was new, ``False`` if it was a duplicate (in
        which case the store is unchanged). The boolean lets the gossip layer
        distinguish "worth propagating" from "already seen".
        """
        tx_hash = transaction.tx_hash
        if tx_hash in self._transactions:
            return False
        self._transactions[tx_hash] = transaction
        self._tree.add_leaf(tx_hash)
        return True

    # -- reads ----------------------------------------------------------------

    def __contains__(self, tx_hash: bytes) -> bool:
        return tx_hash in self._transactions

    def __len__(self) -> int:
        return len(self._transactions)

    def __iter__(self) -> Iterator[Transaction]:
        return iter(self._transactions.values())

    def get(self, tx_hash: bytes) -> Transaction | None:
        return self._transactions.get(tx_hash)

    def hashes(self) -> list[bytes]:
        """All accepted transaction hashes, in insertion order."""
        return list(self._transactions.keys())

    def missing(self, tx_hashes: list[bytes]) -> list[bytes]:
        """From ``tx_hashes``, return those we do not yet hold (for pull)."""
        return [h for h in tx_hashes if h not in self._transactions]

    # -- merkle summary -------------------------------------------------------

    @property
    def root(self) -> bytes:
        """Merkle root over all accepted transactions."""
        return self._tree.root

    @property
    def root_hex(self) -> str:
        return self._tree.root_hex

    @property
    def tree(self) -> MerkleTree:
        """Direct access to the underlying tree (e.g. to build proofs)."""
        return self._tree
