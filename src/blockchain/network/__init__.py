"""P2P transport layer (the only IPv8-dependent package).

- ``payloads``  -> the wire format (IPv8 DataClassPayloads).
- ``gossip``    -> pluggable Push / Pull / Hybrid propagation strategies.
- ``community`` -> the IPv8 overlay that ties transactions, mempool, metrics,
  the chosen gossip strategy and the node's chain together.

Transactions and blocks travel differently on purpose. A transaction is small
and its strategy is swappable, because comparing those strategies was the point
of phase 3. A block is large and rare, so it is **announced by hash** and pulled
by whoever does not recognise it -- the shape the group's own measurements
favoured, and the one Bitcoin uses.
"""

from blockchain.network.community import BlockchainCommunity, NodeConfig
from blockchain.network.gossip import GossipStrategy, HybridGossip, PullGossip, PushGossip, make_strategy

__all__ = [
    "BlockchainCommunity",
    "NodeConfig",
    "GossipStrategy",
    "PushGossip",
    "PullGossip",
    "HybridGossip",
    "make_strategy",
]
