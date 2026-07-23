"""Pure domain layer no IPv8, no I/O, no network.

Contains the ledger's value objects: :class:`Transaction` and the
:class:`MerkleTree` used to store accepted transactions. Being dependency-free
makes this layer trivially unit-testable and reusable in later phases
(blocks, consensus, execution).
"""

from blockchain.core.merkle import MerkleTree
from blockchain.core.transaction import Transaction

__all__ = ["Transaction", "MerkleTree"]
