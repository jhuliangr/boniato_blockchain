"""Consensus layer: which blocks exist, and which branch of them is the truth.

The layer the course slides place between infrastructure and execution. Below
it, peers move bytes; above it, the execution layer needs one ordered history to
apply. Turning many peers' independent Proof-of-Work into that single history is
what happens here.

Module map:

- :mod:`~blockchain.consensus.validation` -- the rules a block must satisfy.
- :mod:`~blockchain.consensus.chain` -- the block *tree*, cumulative work, fork
  choice, reorganisation and the orphan pool.
- :mod:`~blockchain.consensus.ledger` -- chain and execution held together, so
  the head always has a world state and the mempool follows the head.
- :mod:`~blockchain.consensus.miner` -- Proof-of-Work in bounded rounds, so a
  node can mine without going deaf to its peers.

The layer is pure Python: it has no IPv8 dependency and no I/O, so a fork, a
reorganisation or a replayed transaction can all be provoked in a unit test
without a network.
"""

from blockchain.consensus.chain import (
    DUPLICATE,
    EXTENDED,
    INVALID,
    ORPHAN,
    REORG,
    SIDE,
    Chain,
    ChainEntry,
    ChainUpdate,
    work_of,
)
from blockchain.consensus.ledger import (
    DEFAULT_DIFFICULTY,
    BlockOutcome,
    Ledger,
    LedgerUpdate,
)
from blockchain.consensus.miner import Miner
from blockchain.consensus.validation import MAX_BLOCK_TRANSACTIONS, check_block

__all__ = [
    # chain
    "Chain",
    "ChainEntry",
    "ChainUpdate",
    "work_of",
    "DUPLICATE",
    "INVALID",
    "ORPHAN",
    "SIDE",
    "EXTENDED",
    "REORG",
    # validation
    "check_block",
    "MAX_BLOCK_TRANSACTIONS",
    # ledger
    "Ledger",
    "LedgerUpdate",
    "BlockOutcome",
    "DEFAULT_DIFFICULTY",
    # mining
    "Miner",
]
