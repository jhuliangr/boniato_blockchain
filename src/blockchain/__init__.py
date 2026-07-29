"""Decentralized ledger built on top of py-ipv8.

The package is organised in layers, each with a single responsibility:

- ``blockchain.crypto``    -> asymmetric identity: keys, signing, verification.
- ``blockchain.core``      -> pure domain models: Transaction, Merkle tree, Block, PoW.
- ``blockchain.storage``   -> persistence of accepted transactions (Merkle-backed).
- ``blockchain.execution`` -> the state machine: world state and its transitions.
- ``blockchain.access``    -> the chain over HTTP, for a browser client.
- ``blockchain.network``   -> P2P transport: IPv8 payloads, community and gossip.
- ``blockchain.metrics``   -> instrumentation for the gossip experiments.
- ``blockchain.topology``  -> network topology capture and export.

Only the ``network`` layer depends on IPv8; ``core``, ``storage``, ``execution``
and ``access`` are pure Python so they can be unit-tested in isolation. ``access``
uses nothing beyond the standard library.

``execution`` hosts the DApp: a sweet-potato "crop-to-earn" economy. It sits
above consensus and below access, and it is the only layer that understands what
a transaction *means*. Everything below it treats an application payload as
opaque bytes, so the game can grow without touching the ledger.
"""

__all__ = [
    "crypto",
    "core",
    "storage",
    "execution",
    "access",
    "network",
    "metrics",
    "topology",
]
