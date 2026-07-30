"""Access layer: the chain, exposed over HTTP.

The tier between execution and application in the layered model the course
follows. It answers "what does the world look like?" and "please do this", so a
client that cannot itself hold a chain, or sign with IPv8's curve, can still
play.

- :mod:`~blockchain.access.node` one node's chain, mempool and demo keyring.
- :mod:`~blockchain.access.routes` paths and verbs to status codes and dicts.
- :mod:`~blockchain.access.server` the standard-library HTTP transport.

Routing is a pure function of the node, so every endpoint is testable without
opening a socket. The wire contract is written down in ``docs/api.md``, and the
React client in ``web/`` is built against the same document.
"""

from blockchain.access.node import DEFAULT_DIFFICULTY, FarmNode, MinedBlock, Wallet
from blockchain.access.routes import ApiError, handle
from blockchain.access.server import build_server

__all__ = [
    "FarmNode",
    "MinedBlock",
    "Wallet",
    "DEFAULT_DIFFICULTY",
    "handle",
    "ApiError",
    "build_server",
]
