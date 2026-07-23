"""The IPv8 overlay that turns a process into a ledger peer.

This is the composition root of the P2P layer. It wires together:

- a :class:`~blockchain.crypto.Identity` (who I am / how I sign),
- a :class:`~blockchain.storage.Mempool` (what I have accepted),
- a :class:`~blockchain.network.gossip.GossipStrategy` (how I propagate),
- a :class:`~blockchain.metrics.Metrics` collector (what it cost).

The community owns *transport and state*; it delegates *propagation policy* to
the injected strategy, calling it back through the small "gossip context"
interface (``peers`` / ``random_peers`` / ``send_transaction`` /
``request_inventory``) that this class implements.

Per-node configuration is injected via IPv8's ``initialize`` mechanism: IPv8
``setattr``\\s every key of the ``initialize`` dict onto the settings object,
so we declare a :class:`BlockchainSettings` carrying a :class:`NodeConfig`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from ipv8.community import Community, CommunitySettings
from ipv8.lazy_community import lazy_wrapper
from ipv8.peer import Peer

from blockchain.core import Transaction
from blockchain.crypto import Identity
from blockchain.metrics import Metrics
from blockchain.network.gossip import GossipStrategy
from blockchain.network.payloads import (
    GetTransactionsPayload,
    InventoryPayload,
    InventoryRequestPayload,
    TransactionPayload,
    from_payload,
    pack_hashes,
    to_payload,
    unpack_hashes,
)
from blockchain.storage import Mempool


@dataclass
class NodeConfig:
    """Everything a node needs beyond raw IPv8 transport."""

    identity: Identity
    strategy: GossipStrategy
    metrics: Metrics = field(default_factory=Metrics)
    name: str = "node"
    #: Seconds between locally produced dummy transactions (0 disables).
    tx_interval: float = 5.0
    #: Seconds between gossip ticks (pull polling / hybrid safety net).
    tick_interval: float = 2.0
    #: Optional seed for reproducible peer selection.
    rng_seed: int | None = None


class BlockchainSettings(CommunitySettings):
    """CommunitySettings carrying our per-node configuration.

    ``node`` is populated by IPv8 from the overlay's ``initialize`` dict.
    """

    node: NodeConfig


class BlockchainCommunity(Community):
    """A peer in the decentralized ledger network."""

    # 20-byte identifier shared by every peer of this overlay.
    community_id = b"harbourspaceledger01"
    settings_class = BlockchainSettings

    def __init__(self, settings: BlockchainSettings) -> None:
        super().__init__(settings)
        self.config: NodeConfig = settings.node
        self.mempool = Mempool()
        self.metrics = self.config.metrics
        self._strategy = self.config.strategy
        self._rng = random.Random(self.config.rng_seed)

        # Wire the message handlers (wire number -> handler).
        self.add_message_handler(TransactionPayload, self.on_transaction)
        self.add_message_handler(InventoryRequestPayload, self.on_inventory_request)
        self.add_message_handler(InventoryPayload, self.on_inventory)
        self.add_message_handler(GetTransactionsPayload, self.on_get_transactions)

        # Periodic behaviour: produce dummy transactions and run gossip ticks.
        # The initial delay is randomised per node so that hundreds of peers do
        # not fire in lock-step synchronised bursts otherwise collide and
        # overflow the UDP buffers, which looks like (and causes) packet loss.
        if self.config.tx_interval > 0:
            self.register_task(
                "produce_transaction",
                self._produce_transaction,
                interval=self.config.tx_interval,
                delay=self._rng.uniform(0, self.config.tx_interval),
            )
        if self.config.tick_interval > 0:
            self.register_task(
                "gossip_tick",
                self._gossip_tick,
                interval=self.config.tick_interval,
                delay=self._rng.uniform(0, self.config.tick_interval),
            )

    # -- gossip context (called back by the strategy) ------------------------

    def peers(self) -> list[Peer]:
        """Current verified neighbours in this overlay."""
        return self.get_peers()

    def random_peers(self, k: int) -> list[Peer]:
        """Up to ``k`` distinct random neighbours."""
        peers = self.get_peers()
        if k >= len(peers):
            self._rng.shuffle(peers)
            return peers
        return self._rng.sample(peers, k)

    def send_transaction(self, peer: Peer, transaction) -> None:
        self.ez_send(peer, to_payload(transaction))
        self.metrics.record_sent("tx")

    def request_inventory(self, peer: Peer) -> None:
        token = self._rng.getrandbits(32)
        self.ez_send(peer, InventoryRequestPayload(token=token))
        self.metrics.record_sent("inv_request")

    # -- periodic tasks -------------------------------------------------------

    def _produce_transaction(self) -> None:
        """Create, store and propagate a fresh dummy transaction."""
        transaction = Transaction.create(self.config.identity)
        if self.mempool.add(transaction):
            # Locally originated => no source peer to exclude.
            self._strategy.on_transaction_accepted(self, transaction, source=None)

    def _gossip_tick(self) -> None:
        self._strategy.on_tick(self)

    def pause_production(self) -> None:
        """Stop creating new transactions (gossip keeps running).

        Used by the harness to let the network reach a quiescent state before
        measuring convergence pull/hybrid can then recover transactions that
        were lost during the noisy production phase.
        """
        if self.is_pending_task_active("produce_transaction"):
            self.cancel_pending_task("produce_transaction")

    # -- message handlers -----------------------------------------------------

    @lazy_wrapper(TransactionPayload)
    def on_transaction(self, peer: Peer, payload: TransactionPayload) -> None:
        """Receive a transaction: validate the signature, then accept or drop."""
        self.metrics.record_received("tx")
        transaction = from_payload(payload)

        # Phase-2 rule: verify the signature against the enclosed public key.
        if not transaction.is_valid():
            return  # invalid signature -> discard silently

        is_new = self.mempool.add(transaction)
        self.metrics.record_transaction(is_new=is_new)
        if is_new:
            self._strategy.on_transaction_accepted(self, transaction, source=peer)

    @lazy_wrapper(InventoryRequestPayload)
    def on_inventory_request(self, peer: Peer, payload: InventoryRequestPayload) -> None:
        """Answer a pull poll with the hashes of everything we hold."""
        self.metrics.record_received("inv_request")
        self.ez_send(peer, InventoryPayload(token=payload.token, hashes=pack_hashes(self.mempool.hashes())))
        self.metrics.record_sent("inventory")

    @lazy_wrapper(InventoryPayload)
    def on_inventory(self, peer: Peer, payload: InventoryPayload) -> None:
        """On receiving an inventory, request whatever we are missing."""
        self.metrics.record_received("inventory")
        missing = self.mempool.missing(unpack_hashes(payload.hashes))
        if missing:
            self.ez_send(peer, GetTransactionsPayload(hashes=pack_hashes(missing)))
            self.metrics.record_sent("get_tx")

    @lazy_wrapper(GetTransactionsPayload)
    def on_get_transactions(self, peer: Peer, payload: GetTransactionsPayload) -> None:
        """Serve specific transactions requested during a pull exchange."""
        self.metrics.record_received("get_tx")
        for tx_hash in unpack_hashes(payload.hashes):
            transaction = self.mempool.get(tx_hash)
            if transaction is not None:
                self.send_transaction(peer, transaction)

    # -- reporting ------------------------------------------------------------

    def node_summary(self) -> dict:
        """A snapshot of this node's state for reports / dashboards."""
        return {
            "name": self.config.name,
            "address": self.config.identity.address[:16],
            "strategy": self._strategy.name,
            "peers": len(self.get_peers()),
            "mempool_size": len(self.mempool),
            "merkle_root": self.mempool.root_hex[:16],
            "metrics": self.metrics.to_dict(),
        }
