"""Decentralized ledger built on top of py-ipv8.

The package is organised in layers, each with a single responsibility:

- ``blockchain.crypto``   -> asymmetric identity: keys, signing, verification.
- ``blockchain.core``     -> pure domain models: Transaction and the Merkle tree.
- ``blockchain.storage``  -> persistence of accepted transactions (Merkle-backed).
- ``blockchain.network``  -> P2P transport: IPv8 payloads, community and gossip.
- ``blockchain.metrics``  -> instrumentation for the gossip experiments.
- ``blockchain.topology`` -> network topology capture and export.

Only the ``network`` layer depends on IPv8; ``core`` and ``storage`` are pure
Python so they can be unit-tested in isolation.
"""

__all__ = ["crypto", "core", "storage", "network", "metrics", "topology"]
