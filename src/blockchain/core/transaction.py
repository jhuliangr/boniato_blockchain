"""The :class:`Transaction` value object.

Phase-2 transactions were intentionally minimal (no balances yet), matching the
brief:

    Transaction { nonce, public_key, signature }

They now carry one further field, ``action``: the opaque bytes describing what
the sender wants the chain to *do*. An empty action is still exactly the
phase-2 dummy transaction, which is what the gossip experiments produce.

Design decisions:

- The transaction is an **immutable** value object (``frozen`` dataclass). Once
  created it never changes, which makes hashing and dedup safe.
- The core layer treats ``action`` as **opaque bytes**. It signs them, hashes
  them and bounds their size, but never parses them; their meaning belongs to
  :mod:`blockchain.execution.actions`. That keeps consensus, gossip and mining
  entirely unaware of the application running on top, so the DApp can gain
  operations without a single change down here.
- What gets *signed* (:attr:`signing_payload`) is deliberately separate from
  what *identifies* the transaction (:attr:`tx_hash`). The signature commits to
  the ``nonce`` + ``public_key`` + ``action``; the id additionally commits to
  the signature, so two distinct signatures over the same nonce are distinct
  transactions.
- The class knows how to sign/verify itself but delegates the actual crypto to
  the :mod:`blockchain.crypto` layer it never imports IPv8 directly.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from blockchain.crypto import Identity, verify

# A domain-separation tag mixed into the signed digest so a transaction
# signature can never be mistaken for a signature over some other message type.
_DOMAIN_TAG = b"harbourspace-tx-v1"

# The nonce travels on the wire as IPv8's signed 64-bit integer ('q'), so it is
# constrained to the non-negative signed-64-bit range. Eight bytes is still the
# width we serialize for hashing.
NONCE_BYTES = 8
_NONCE_MAX = (1 << 63) - 1

# Upper bound on the action payload. Actions are small, structured commands (see
# :mod:`blockchain.execution.actions`), so a tight ceiling costs nothing and
# stops a peer from using the field to flood the mempool with bulk data.
MAX_ACTION_BYTES = 256
_ACTION_LEN_BYTES = 2


@dataclass(frozen=True)
class Transaction:
    """A signed transaction exchanged between peers."""

    nonce: int
    public_key: bytes
    signature: bytes
    #: Opaque application payload; empty for a phase-2 dummy transaction.
    action: bytes = b""

    # -- construction ---------------------------------------------------------

    @classmethod
    def create(
        cls,
        identity: Identity,
        nonce: int | None = None,
        action: bytes = b"",
    ) -> "Transaction":
        """Build and sign a fresh transaction for ``identity``.

        A random 64-bit nonce is generated when none is supplied.
        """
        if nonce is None:
            nonce = secrets.randbelow(_NONCE_MAX + 1)
        if len(action) > MAX_ACTION_BYTES:
            raise ValueError(f"action exceeds {MAX_ACTION_BYTES} bytes")
        signature = identity.sign(_digest(nonce, identity.public_key, action))
        return cls(
            nonce=nonce,
            public_key=identity.public_key,
            signature=signature,
            action=action,
        )

    # -- identity / hashing ---------------------------------------------------

    @property
    def signing_payload(self) -> bytes:
        """The exact bytes a valid signature must cover."""
        return _digest(self.nonce, self.public_key, self.action)

    @property
    def tx_hash(self) -> bytes:
        """Unique 32-byte identifier of this transaction."""
        h = hashlib.sha256()
        h.update(self.nonce.to_bytes(NONCE_BYTES, "big"))
        h.update(self.public_key)
        h.update(self.signature)
        h.update(len(self.action).to_bytes(_ACTION_LEN_BYTES, "big"))
        h.update(self.action)
        return h.digest()

    @property
    def tx_id(self) -> str:
        """Hex form of :attr:`tx_hash`, convenient for logs and dicts."""
        return self.tx_hash.hex()

    # -- validation -----------------------------------------------------------

    def is_valid(self) -> bool:
        """Return ``True`` iff the signature matches the embedded public key.

        This is the check a receiving peer runs before accepting/storing the
        transaction. It is total: any malformed field yields ``False``.
        """
        if not (0 <= self.nonce <= _NONCE_MAX):
            return False
        if len(self.action) > MAX_ACTION_BYTES:
            return False
        return verify(self.public_key, self.signing_payload, self.signature)

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        kind = f", action={self.action[:1].hex()}" if self.action else ""
        return f"Transaction(nonce={self.nonce}, id={self.tx_id[:12]}…{kind})"


def _digest(nonce: int, public_key: bytes, action: bytes = b"") -> bytes:
    """Deterministic digest of the signable content of a transaction.

    The action is length-prefixed so that no two distinct (public_key, action)
    pairs can produce the same digest by shifting the boundary between them.
    """
    h = hashlib.sha256()
    h.update(_DOMAIN_TAG)
    h.update(nonce.to_bytes(NONCE_BYTES, "big"))
    h.update(public_key)
    h.update(len(action).to_bytes(_ACTION_LEN_BYTES, "big"))
    h.update(action)
    return h.digest()
