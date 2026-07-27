"""Pure domain layer no IPv8, no I/O, no network.

Contains the ledger's value objects: :class:`Transaction` and the
:class:`MerkleTree` used to store accepted transactions. Being dependency-free
makes this layer trivially unit-testable and reusable in later phases
(blocks, consensus, execution).
"""

from blockchain.core.block import GENESIS_PREV_HASH, Block
from blockchain.core.merkle import MerkleTree
from blockchain.core.pow import (
    DEFAULT_DIFFICULTY,
    has_proof_of_work,
    leading_zero_bits,
    mine,
)
from blockchain.core.transaction import Transaction

__all__ = [
    "Transaction",
    "MerkleTree",
    "Block",
    "GENESIS_PREV_HASH",
    "mine",
    "has_proof_of_work",
    "leading_zero_bits",
    "DEFAULT_DIFFICULTY",
]
