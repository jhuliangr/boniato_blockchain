"""Decentralized ledger built on top of py-ipv8.

The package is organised in layers, each with a single responsibility:

- ``blockchain.crypto``    -> asymmetric identity: keys, signing, verification.
- ``blockchain.core``      -> pure domain models: Transaction, Merkle tree, Block, PoW.
- ``blockchain.storage``   -> persistence of accepted transactions (Merkle-backed).
- ``blockchain.consensus`` -> the block tree, fork choice, reorgs and mining.
- ``blockchain.execution`` -> the state machine: world state and its transitions.
- ``blockchain.access``    -> the chain over HTTP, for a browser client.
- ``blockchain.network``   -> P2P transport: IPv8 payloads, community and gossip.
- ``blockchain.metrics``   -> instrumentation for the gossip experiments.
- ``blockchain.topology``  -> network topology capture and export.

Only the ``network`` layer depends on IPv8; ``core``, ``storage``, ``consensus``,
``execution`` and ``access`` are pure Python so they can be unit-tested in
isolation -- a fork, a reorganisation or a replayed transaction can all be
provoked without opening a socket. ``access`` uses nothing beyond the standard
library.

``consensus`` and ``execution`` are the two halves of a working node and are
joined in exactly one place, :class:`blockchain.consensus.Ledger`: consensus
decides *which* blocks exist, execution decides what they *mean*. Both node
shells -- the HTTP one in ``access`` and the P2P one in ``network`` -- are built on
that single object, so they cannot drift apart in how a block is applied.

``execution`` hosts the DApp: a sweet-potato "crop-to-earn" economy. It is the
only layer that understands what a transaction *means*; everything below it
treats an application payload as opaque bytes, so the game can grow without
touching the ledger.
"""

__all__ = [
    "crypto",
    "core",
    "storage",
    "consensus",
    "execution",
    "access",
    "network",
    "metrics",
    "topology",
]
