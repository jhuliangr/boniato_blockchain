"""P2P transport layer (the only IPv8-dependent package).

- ``payloads``  -> the wire format (IPv8 DataClassPayloads).
- ``gossip``    -> pluggable Push / Pull / Hybrid propagation strategies.
- ``community`` -> the IPv8 overlay that ties transactions, mempool, metrics
  and the chosen gossip strategy together.
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
