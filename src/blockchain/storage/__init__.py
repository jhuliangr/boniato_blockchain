"""Storage layer for accepted transactions.

Currently an in-memory pool backed by a Merkle tree. The brief allows in-memory,
SQLite or a KV store; :class:`Mempool` isolates that decision so a persistent
backend can be dropped in later without touching the network layer.
"""

from blockchain.storage.mempool import Mempool

__all__ = ["Mempool"]
