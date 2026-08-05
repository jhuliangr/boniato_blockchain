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

from blockchain.consensus import DEFAULT_DIFFICULTY, Ledger, Miner
from blockchain.consensus.miner import DEFAULT_HASHES_PER_ROUND
from blockchain.core import Transaction
from blockchain.crypto import Identity
from blockchain.execution import Economy
from blockchain.metrics import Metrics
from blockchain.network.gossip import GossipStrategy
from blockchain.network.payloads import (
    BlockPayload,
    GetBlockPayload,
    GetTransactionsPayload,
    HeadPayload,
    InventoryPayload,
    InventoryRequestPayload,
    TransactionPayload,
    block_from_payload,
    block_to_payload,
    from_payload,
    pack_hashes,
    to_payload,
    unpack_hashes,
)
from blockchain.storage import Mempool


@dataclass
class NodeConfig:
    """Everything a node needs beyond raw IPv8 transport.

    The consensus settings all default to *off*, so a node built from this class
    behaves exactly as it did before blocks existed. That is deliberate: the
    phase-3 gossip experiments measure transaction dissemination, and a node
    that also mined and announced blocks would quietly change the numbers in
    ``docs/design-and-analysis.md``. Mining is opt-in per experiment.
    """

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

    # -- consensus ------------------------------------------------------------
    #: Shared chain parameters. Every node must agree on these or they are not
    #: on the same network: the genesis hash and every block's validity depend
    #: on them.
    difficulty: int = DEFAULT_DIFFICULTY
    economy: Economy | None = None
    #: Seconds between mining rounds (0 disables mining: the node still relays,
    #: validates and follows the chain, it just never proposes a block).
    mine_interval: float = 0.0
    #: Nonces per round. With ``mine_interval`` this *is* the node's hash rate,
    #: so an experiment can hand out deliberately unequal mining power.
    hashes_per_round: int = DEFAULT_HASHES_PER_ROUND
    #: Seconds between head announcements (0 disables). The recovery path: a
    #: node that missed an announcement, or joined late, learns the tip here.
    announce_interval: float = 0.0


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

        # The chain this node believes, and the state that follows from it. Its
        # genesis is derived from the shared parameters alone, so every node
        # that agrees on the difficulty and the economy starts from the same
        # block hash without anyone distributing one.
        self.ledger = Ledger(economy=self.config.economy, difficulty=self.config.difficulty)
        self.miner = Miner(self.ledger, hashes_per_round=self.config.hashes_per_round)
        #: Called with every :class:`LedgerUpdate` that moved this node's head.
        #: An observation seam, not a feature: measuring when a block *arrived*
        #: is impossible from outside without polling, and polling would put the
        #: sampling interval into every latency number we report.
        self._block_listeners: list = []

        # Wire the message handlers (wire number -> handler).
        self.add_message_handler(TransactionPayload, self.on_transaction)
        self.add_message_handler(InventoryRequestPayload, self.on_inventory_request)
        self.add_message_handler(InventoryPayload, self.on_inventory)
        self.add_message_handler(GetTransactionsPayload, self.on_get_transactions)
        self.add_message_handler(HeadPayload, self.on_head)
        self.add_message_handler(GetBlockPayload, self.on_get_block)
        self.add_message_handler(BlockPayload, self.on_block)

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
        if self.config.mine_interval > 0:
            self.register_task(
                "mine",
                self._mine_round,
                interval=self.config.mine_interval,
                delay=self._rng.uniform(0, self.config.mine_interval),
            )
        if self.config.announce_interval > 0:
            self.register_task(
                "announce_head",
                self._announce_head,
                interval=self.config.announce_interval,
                delay=self._rng.uniform(0, self.config.announce_interval),
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
        self.submit(transaction)

    def submit(self, transaction: Transaction) -> bool:
        """Accept a locally created transaction: queue it and propagate it.

        The entry point a client uses. Returns whether it was new to this node.
        """
        if not self.mempool.add(transaction):
            return False
        self.ledger.submit(transaction)
        # Locally originated => no source peer to exclude.
        self._strategy.on_transaction_accepted(self, transaction, source=None)
        return True

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

    def pause_mining(self) -> None:
        """Stop proposing blocks; keep relaying, validating and announcing.

        The end phase of a measurement. Asking whether the network agrees while
        blocks are still being found measures a race, not a property: the last
        block is always somewhere in flight, and a node that has not received it
        yet looks like a node that disagrees. Stopping the miners first and
        letting the tail propagate is what makes "did they converge?" a question
        with an answer.
        """
        if self.is_pending_task_active("mine"):
            self.cancel_pending_task("mine")

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
            # The mempool is what this node has *seen* (and what the gossip
            # layer dedups against); the ledger's queue is what is still
            # unmined. A transaction that was already mined stays in the former
            # and is gone from the latter, so a peer can still be served it
            # while this node will not try to mine it a second time.
            self.ledger.submit(transaction)
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

    # -- consensus ------------------------------------------------------------

    def _mine_round(self) -> None:
        """Spend one bounded round searching for a nonce, and publish a hit."""
        block = self.miner.step()
        if block is None:
            return
        self.metrics.record_mined(self.miner.hashes_tried)
        # A block this node mined goes through exactly the same door as one from
        # a peer. Nothing gets to skip validation for having been produced
        # locally -- if our own block is not acceptable, we need to find that out
        # here rather than after announcing it to the network.
        self._connect(block, source=None)

    def _connect(self, block, source: Peer | None) -> None:
        """Offer a block to the ledger and act on what consensus decided."""
        update = self.ledger.connect(block)
        self.metrics.record_block(update.status)

        if update.status == "orphan" and source is not None:
            # We are behind, or packets arrived out of order. Walk backwards one
            # block at a time until we reach ground we recognise. Costs a round
            # trip per missing block; a production chain sends a block locator
            # and syncs headers first, which turns the walk into one exchange.
            self._request_block(source, block.prev_hash)
            return

        if update.reorg_depth:
            self.metrics.record_reorg(update.reorg_depth)

        if update.head_moved:
            # Only a new head is worth telling anyone about. Announcing a block
            # that lost its race would send peers chasing a branch we ourselves
            # do not follow.
            self._announce_head()
            self.miner.reset()  # whatever we were mining is now on top of the wrong parent
            for listener in self._block_listeners:
                listener(self, update)

    def add_block_listener(self, listener) -> None:
        """Observe every head move on this node, as it happens.

        Used by the benchmark harness to timestamp arrivals. A listener that
        raises would take the message handler down with it, so keep them dumb.
        """
        self._block_listeners.append(listener)

    def _announce_head(self) -> None:
        """Tell every neighbour where our best chain currently ends."""
        head = self.ledger.chain.head_hash
        height = self.ledger.height
        for peer in self.get_peers():
            self.ez_send(peer, HeadPayload(block_hash=head, height=height))
            self.metrics.record_sent("head")

    def _request_block(self, peer: Peer, block_hash: bytes) -> None:
        self.ez_send(peer, GetBlockPayload(block_hash=block_hash))
        self.metrics.record_sent("get_block")

    @lazy_wrapper(HeadPayload)
    def on_head(self, peer: Peer, payload: HeadPayload) -> None:
        """A neighbour named its head. Ask for it if we have never seen it."""
        self.metrics.record_received("head")
        if payload.block_hash in self.ledger.chain:
            return  # already known: either ours, or a branch we already judged
        self._request_block(peer, payload.block_hash)

    @lazy_wrapper(GetBlockPayload)
    def on_get_block(self, peer: Peer, payload: GetBlockPayload) -> None:
        """Serve a block by hash, from any branch we hold.

        Any branch, not just the active one: a peer walking backwards to fill a
        gap is asking about *its* history, and refusing to serve a block because
        we happen to prefer a different branch would strand it.
        """
        self.metrics.record_received("get_block")
        block = self.ledger.chain.get(payload.block_hash)
        if block is not None:
            self.ez_send(peer, block_to_payload(block))
            self.metrics.record_sent("block")

    @lazy_wrapper(BlockPayload)
    def on_block(self, peer: Peer, payload: BlockPayload) -> None:
        """Receive a block: validate it, connect it, follow the heaviest chain."""
        self.metrics.record_received("block")
        self._connect(block_from_payload(payload), source=peer)

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
            # The chain this node believes. Two nodes agreeing on `height` but
            # not on `head` have forked; agreeing on `head` but not on
            # `state_root` would mean the execution layer is non-deterministic,
            # which is the failure the whole engine is built to prevent.
            "height": self.ledger.height,
            "head": self.ledger.chain.head_hash.hex()[:16],
            "state_root": self.ledger.state_root[:16],
            "pending": len(self.ledger),
            "known_blocks": len(self.ledger.chain),
            "branches": self.ledger.chain.branch_count(),
            "metrics": self.metrics.to_dict(),
        }
