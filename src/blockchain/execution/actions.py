"""What a transaction *asks the chain to do*, and how it travels as bytes.

Until now a transaction was pure proof of authorship: a nonce, a public key and
a signature over them. To run a DApp the chain needs transactions that carry
*intent*, so :class:`~blockchain.core.transaction.Transaction` gained one opaque
``action`` field. This module owns the meaning of those bytes.

The split is deliberate. The core layer signs and hashes the action without
understanding it, exactly as Ethereum's transaction envelope is indifferent to
the calldata it carries. All game semantics live here and in
:mod:`blockchain.execution.engine`, which means the ledger, the gossip protocol
and the mining code need no changes when the game gains an action.

Wire format: a one-byte type tag followed by fixed-width big-endian fields.

======  =============  ============================================
 tag     action         body
======  =============  ============================================
 0x01    Claim          (empty)
 0x02    Transfer       amount:8, key_len:2, recipient:key_len
 0x03    BuyLand        (empty)
 0x04    Plant          land_id:4
 0x05    Harvest        land_id:4
 0x06    Fertilize      land_id:4, amount:8
======  =============  ============================================

:func:`decode` is **total**: any malformed blob yields ``None`` rather than an
exception, because it is fed straight from the network. That mirrors
:func:`blockchain.crypto.verify`, which returns ``False`` on a malformed key.
"""

from __future__ import annotations

from dataclasses import dataclass

from blockchain.core.transaction import MAX_ACTION_BYTES, Transaction
from blockchain.crypto import Identity

# Type tags. Never reuse or renumber a tag: it is committed to by signatures.
TAG_CLAIM = 0x01
TAG_TRANSFER = 0x02
TAG_BUY_LAND = 0x03
TAG_PLANT = 0x04
TAG_HARVEST = 0x05
TAG_FERTILIZE = 0x06

_AMOUNT_BYTES = 8
_KEY_LEN_BYTES = 2
_LAND_ID_BYTES = 4

#: Ceilings implied by the field widths above. Enforced at encode time so an
#: out-of-range value fails loudly at the source instead of silently wrapping.
MAX_AMOUNT = (1 << (_AMOUNT_BYTES * 8)) - 1
MAX_LAND_ID = (1 << (_LAND_ID_BYTES * 8)) - 1
MAX_KEY_BYTES = (1 << (_KEY_LEN_BYTES * 8)) - 1


@dataclass(frozen=True)
class Claim:
    """Ask for the one-time starter kit: some $BONI and a first plot.

    Self-serve onboarding. A chain with no faucet and no pre-mine needs *some*
    way for a fresh key to acquire its first boniato, and doing it on-chain
    keeps the genesis state free of privileged accounts. The engine allows it
    once per public key.
    """

    name = "claim"

    def encode(self) -> bytes:
        return bytes([TAG_CLAIM])


@dataclass(frozen=True)
class Transfer:
    """Send ``amount`` base units of $BONI to ``recipient``."""

    recipient: bytes
    amount: int

    name = "transfer"

    def encode(self) -> bytes:
        _require(0 <= self.amount <= MAX_AMOUNT, "amount out of range")
        _require(0 < len(self.recipient) <= MAX_KEY_BYTES, "recipient key out of range")
        return (
            bytes([TAG_TRANSFER])
            + self.amount.to_bytes(_AMOUNT_BYTES, "big")
            + len(self.recipient).to_bytes(_KEY_LEN_BYTES, "big")
            + self.recipient
        )


@dataclass(frozen=True)
class BuyLand:
    """Buy the next plot on the map at the current market price.

    The price is not a parameter: it is read from world state at execution time.
    Naming a price here would let a buyer front-run the curve by signing at
    yesterday's rate.
    """

    name = "buy_land"

    def encode(self) -> bytes:
        return bytes([TAG_BUY_LAND])


@dataclass(frozen=True)
class Plant:
    """Burn a seed to start a crop on a plot you own."""

    land_id: int

    name = "plant"

    def encode(self) -> bytes:
        _require(0 <= self.land_id <= MAX_LAND_ID, "land_id out of range")
        return bytes([TAG_PLANT]) + self.land_id.to_bytes(_LAND_ID_BYTES, "big")


@dataclass(frozen=True)
class Harvest:
    """Collect a grown crop, minting $BONI according to the economy's rules."""

    land_id: int

    name = "harvest"

    def encode(self) -> bytes:
        _require(0 <= self.land_id <= MAX_LAND_ID, "land_id out of range")
        return bytes([TAG_HARVEST]) + self.land_id.to_bytes(_LAND_ID_BYTES, "big")


@dataclass(frozen=True)
class Fertilize:
    """Spend compost on a growing crop to bring its harvest forward.

    ``amount`` is an offer, not a charge: the engine consumes only what buys
    whole blocks of growth up to the floor set by ``fertilizer_min_growth_bp``
    and leaves the rest in the heap. A player can therefore throw their whole
    supply at a plot without having to compute the exact amount that fits.
    """

    land_id: int
    amount: int

    name = "fertilize"

    def encode(self) -> bytes:
        _require(0 <= self.land_id <= MAX_LAND_ID, "land_id out of range")
        _require(0 <= self.amount <= MAX_AMOUNT, "amount out of range")
        return (
            bytes([TAG_FERTILIZE])
            + self.land_id.to_bytes(_LAND_ID_BYTES, "big")
            + self.amount.to_bytes(_AMOUNT_BYTES, "big")
        )


#: Every action type, i.e. the chain's whole instruction set.
Action = Claim | Transfer | BuyLand | Plant | Harvest | Fertilize


# -- codec --------------------------------------------------------------------


def decode(blob: bytes) -> Action | None:
    """Parse an action from the wire, or ``None`` if the bytes are not one.

    Trailing bytes are rejected rather than ignored: an action must have exactly
    one encoding, otherwise two distinct blobs could mean the same thing and a
    transaction could be replayed under a different hash.
    """
    if not blob or len(blob) > MAX_ACTION_BYTES:
        return None

    tag, body = blob[0], blob[1:]
    if tag == TAG_CLAIM:
        return Claim() if not body else None
    if tag == TAG_BUY_LAND:
        return BuyLand() if not body else None
    if tag == TAG_TRANSFER:
        return _decode_transfer(body)
    if tag == TAG_PLANT:
        land_id = _decode_land_id(body)
        return Plant(land_id=land_id) if land_id is not None else None
    if tag == TAG_HARVEST:
        land_id = _decode_land_id(body)
        return Harvest(land_id=land_id) if land_id is not None else None
    if tag == TAG_FERTILIZE:
        return _decode_fertilize(body)
    return None


def _decode_fertilize(body: bytes) -> Fertilize | None:
    if len(body) != _LAND_ID_BYTES + _AMOUNT_BYTES:
        return None
    return Fertilize(
        land_id=int.from_bytes(body[:_LAND_ID_BYTES], "big"),
        amount=int.from_bytes(body[_LAND_ID_BYTES:], "big"),
    )


def _decode_transfer(body: bytes) -> Transfer | None:
    header = _AMOUNT_BYTES + _KEY_LEN_BYTES
    if len(body) < header:
        return None
    amount = int.from_bytes(body[:_AMOUNT_BYTES], "big")
    key_len = int.from_bytes(body[_AMOUNT_BYTES:header], "big")
    recipient = body[header:]
    if key_len == 0 or len(recipient) != key_len:
        return None
    return Transfer(recipient=recipient, amount=amount)


def _decode_land_id(body: bytes) -> int | None:
    if len(body) != _LAND_ID_BYTES:
        return None
    return int.from_bytes(body, "big")


# -- signing ------------------------------------------------------------------


def signed(identity: Identity, action: Action, nonce: int | None = None) -> Transaction:
    """Wrap ``action`` in a transaction signed by ``identity``.

    The convenience bridge between this layer and the core envelope: it is the
    only thing a client (script, REST handler, test) needs to submit an action.
    """
    return Transaction.create(identity, nonce=nonce, action=action.encode())


def _require(condition: bool, message: str) -> None:
    """Guard an encode-time invariant. Encoding is local, so raising is right."""
    if not condition:
        raise ValueError(message)
