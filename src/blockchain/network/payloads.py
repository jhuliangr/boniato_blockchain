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
======  ==========================  ============================================
"""

from __future__ import annotations

from dataclasses import dataclass

from ipv8.messaging.payload_dataclass import DataClassPayload

from blockchain.core import Transaction

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


def pack_hashes(hashes: list[bytes]) -> bytes:
    """Concatenate fixed-size hashes into a single blob for the wire."""
    return b"".join(hashes)


def unpack_hashes(blob: bytes) -> list[bytes]:
    """Split a blob back into fixed-size hashes, ignoring any trailing junk."""
    return [blob[i : i + HASH_SIZE] for i in range(0, len(blob) - HASH_SIZE + 1, HASH_SIZE)]
