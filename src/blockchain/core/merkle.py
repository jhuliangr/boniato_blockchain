"""A binary Merkle tree over transaction hashes.

The Phase-2 brief requires accepted transactions to be stored *as a Merkle
tree* (in memory / SQLite / KV the structure is what matters). This module
implements the structure; :mod:`blockchain.storage` wires it to a concrete
store.

Properties:

- SHA-256 throughout.
- **Domain separation** between leaves (prefix ``0x00``) and internal nodes
  (prefix ``0x01``). This is the standard defence against second-preimage
  attacks where an attacker passes an internal node off as a leaf.
- Odd levels duplicate the last node (Bitcoin-style), so every level halves
  cleanly.
- Supports membership proofs, which the later DApp can use to prove a
  transaction is in the ledger without shipping the whole set.
"""

from __future__ import annotations

import hashlib
from typing import Iterable

_LEAF_PREFIX = b"\x00"
_NODE_PREFIX = b"\x01"

# Root of an empty tree a fixed, well-known value.
EMPTY_ROOT = hashlib.sha256(b"").digest()


def _hash_leaf(data: bytes) -> bytes:
    return hashlib.sha256(_LEAF_PREFIX + data).digest()


def _hash_nodes(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(_NODE_PREFIX + left + right).digest()


class MerkleTree:
    """An append-only Merkle tree.

    Leaves are added in insertion order; the root is recomputed lazily so that
    bursts of insertions cost a single rebuild.
    """

    def __init__(self, leaves: Iterable[bytes] | None = None) -> None:
        self._leaves: list[bytes] = list(leaves or [])
        self._root: bytes | None = None  # cached; ``None`` means "dirty"

    # -- mutation -------------------------------------------------------------

    def add_leaf(self, data: bytes) -> None:
        """Append a leaf (typically a transaction hash) and invalidate cache."""
        self._leaves.append(data)
        self._root = None

    # -- access ---------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._leaves)

    @property
    def leaves(self) -> tuple[bytes, ...]:
        return tuple(self._leaves)

    @property
    def root(self) -> bytes:
        """The Merkle root, recomputed on demand and cached."""
        if self._root is None:
            self._root = self._compute_root()
        return self._root

    @property
    def root_hex(self) -> str:
        return self.root.hex()

    # -- proofs ---------------------------------------------------------------

    def proof(self, index: int) -> list[tuple[bytes, str]]:
        """Return a membership proof for the leaf at ``index``.

        The proof is a list of ``(sibling_hash, side)`` pairs from the bottom
        up, where ``side`` is ``"left"``/``"right"`` telling the verifier where
        the sibling sits. Verify it with :func:`verify_proof`.
        """
        if not 0 <= index < len(self._leaves):
            raise IndexError("leaf index out of range")

        level = [_hash_leaf(leaf) for leaf in self._leaves]
        path: list[tuple[bytes, str]] = []
        idx = index
        while len(level) > 1:
            if len(level) % 2 == 1:
                level.append(level[-1])  # duplicate last to pair up
            if idx % 2 == 0:  # node is a left child; sibling is on the right
                path.append((level[idx + 1], "right"))
            else:  # node is a right child; sibling is on the left
                path.append((level[idx - 1], "left"))
            idx //= 2
            level = [_hash_nodes(level[i], level[i + 1]) for i in range(0, len(level), 2)]
        return path

    # -- internals ------------------------------------------------------------

    def _compute_root(self) -> bytes:
        if not self._leaves:
            return EMPTY_ROOT
        level = [_hash_leaf(leaf) for leaf in self._leaves]
        while len(level) > 1:
            if len(level) % 2 == 1:
                level.append(level[-1])
            level = [_hash_nodes(level[i], level[i + 1]) for i in range(0, len(level), 2)]
        return level[0]


def verify_proof(leaf: bytes, proof: list[tuple[bytes, str]], root: bytes) -> bool:
    """Check that ``leaf`` is included under ``root`` given ``proof``."""
    current = _hash_leaf(leaf)
    for sibling, side in proof:
        if side == "right":
            current = _hash_nodes(current, sibling)
        elif side == "left":
            current = _hash_nodes(sibling, current)
        else:  # pragma: no cover - defensive
            raise ValueError(f"invalid proof side: {side!r}")
    return current == root
