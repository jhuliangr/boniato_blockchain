"""IPv8 wire payloads and their mapping to/from domain objects.

Each payload is a :class:`DataClassPayload` with a unique message number. The
network layer speaks these on the wire; converters keep the domain
:class:`Transaction` free of any serialization concern.

Message catalogue:

======  ==========================  ============================================
 num     payload                     purpose
======  ==========================  ============================================
  1      TransactionPayload          a single (signed) transaction used by Push
  2      InventoryRequestPayload     "tell me what you have" used by Pull
  3      InventoryPayload            list of tx hashes I hold Pull response
  4      GetTransactionsPayload      "send me these specific txs" Pull follow-up
  5      HeadPayload                 "my best chain ends here" block announcement
  6      GetBlockPayload             "send me this block" by hash
  7      BlockPayload                a whole block, transactions included
======  ==========================  ============================================

**Blocks are announced, not pushed.** A head announcement is 40 bytes; a full
block is kilobytes. Flooding blocks to every neighbour would send most peers a
block they already have, which is precisely the failure the group's own gossip
measurements found for eager push (see ``docs/design-and-analysis.md``). So a
node announces the *hash* of its new head and lets peers that do not recognise
it ask for the block -- the same announce-then-pull shape Bitcoin uses for
``inv``/``getdata``, arrived at here from our own numbers.
"""

from __future__ import annotations

from dataclasses import dataclass

from ipv8.messaging.payload_dataclass import DataClassPayload

from blockchain.core import Block, Transaction

HASH_SIZE = 32  # bytes, SHA-256


@dataclass
class TransactionPayload(DataClassPayload[1]):
    """A whole transaction pushed to a peer.

    ``action`` carries the application payload as opaque bytes (empty for a
    phase-2 dummy transaction). The wire format stays indifferent to what the
    DApp encodes in there, so adding a game operation never touches this layer.
    """

    nonce: int
    public_key: bytes
    signature: bytes
    action: bytes


@dataclass
class InventoryRequestPayload(DataClassPayload[2]):
    """Poll a neighbour for its inventory. ``token`` correlates request/reply."""

    token: int


@dataclass
class InventoryPayload(DataClassPayload[3]):
    """Advertised inventory: transaction hashes concatenated into one blob."""

    token: int
    hashes: bytes


@dataclass
class GetTransactionsPayload(DataClassPayload[4]):
    """Request specific transactions by their concatenated hashes."""

    hashes: bytes


@dataclass
class HeadPayload(DataClassPayload[5]):
    """"My best chain ends at this block."

    ``height`` is not needed to decide anything -- the receiver either knows the
    hash or does not -- but it makes a packet capture and the demo log readable,
    and it lets a node tell "I am behind" from "we disagree" at a glance.
    """

    block_hash: bytes
    height: int


@dataclass
class GetBlockPayload(DataClassPayload[6]):
    """Ask for one block by hash. Also how a node walks back to fill a gap."""

    block_hash: bytes


@dataclass
class BlockPayload(DataClassPayload[7]):
    """A whole block: the header fields plus every transaction it carries.

    ``merkle_root`` travels explicitly rather than being recomputed on arrival.
    It is what the sender *claimed*, and the receiver has to be able to catch it
    disagreeing with the bodies -- recomputing it would quietly repair a forged
    block instead of rejecting it.
    """

    index: int
    prev_hash: bytes
    merkle_root: bytes
    timestamp: int
    nonce: int
    transactions: [TransactionPayload]


# -- converters ---------------------------------------------------------------


def to_payload(transaction: Transaction) -> TransactionPayload:
    return TransactionPayload(
        nonce=transaction.nonce,
        public_key=transaction.public_key,
        signature=transaction.signature,
        action=transaction.action,
    )


def from_payload(payload: TransactionPayload) -> Transaction:
    return Transaction(
        nonce=payload.nonce,
        public_key=payload.public_key,
        signature=payload.signature,
        action=payload.action,
    )


def block_to_payload(block: Block) -> BlockPayload:
    return BlockPayload(
        index=block.index,
        prev_hash=block.prev_hash,
        merkle_root=block.merkle_root,
        timestamp=block.timestamp,
        nonce=block.nonce,
        transactions=[to_payload(tx) for tx in block.transactions],
    )


def block_from_payload(payload: BlockPayload) -> Block:
    """Rebuild a block **exactly as sent**, forged fields and all.

    Constructed field by field rather than through :meth:`Block.create`, which
    derives the Merkle root from the transactions and would therefore hand the
    consensus layer a block that always looks internally consistent. Validation
    can only reject what it is allowed to see.
    """
    return Block(
        index=payload.index,
        prev_hash=payload.prev_hash,
        merkle_root=payload.merkle_root,
        timestamp=payload.timestamp,
        nonce=payload.nonce,
        transactions=tuple(from_payload(tx) for tx in payload.transactions),
    )


def pack_hashes(hashes: list[bytes]) -> bytes:
    """Concatenate fixed-size hashes into a single blob for the wire."""
    return b"".join(hashes)


def unpack_hashes(blob: bytes) -> list[bytes]:
    """Split a blob back into fixed-size hashes, ignoring any trailing junk."""
    return [blob[i : i + HASH_SIZE] for i in range(0, len(blob) - HASH_SIZE + 1, HASH_SIZE)]
