"""The :class:`Transaction` value object.

Phase-2 transactions are intentionally minimal (no balances yet), matching the
brief:

    Transaction { nonce, public_key, signature }

Design decisions:

- The transaction is an **immutable** value object (``frozen`` dataclass). Once
  created it never changes, which makes hashing and dedup safe.
- What gets *signed* (:attr:`signing_payload`) is deliberately separate from
  what *identifies* the transaction (:attr:`tx_hash`). The signature commits to
  the ``nonce`` + ``public_key``; the id additionally commits to the signature,
  so two distinct signatures over the same nonce are distinct transactions.
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


@dataclass(frozen=True)
class Transaction:
    """A signed, dummy transaction exchanged between peers."""

    nonce: int
    public_key: bytes
    signature: bytes

    # -- construction ---------------------------------------------------------

    @classmethod
    def create(cls, identity: Identity, nonce: int | None = None) -> "Transaction":
        """Build and sign a fresh transaction for ``identity``.

        A random 64-bit nonce is generated when none is supplied.
        """
        if nonce is None:
            nonce = secrets.randbelow(_NONCE_MAX + 1)
        signature = identity.sign(_digest(nonce, identity.public_key))
        return cls(nonce=nonce, public_key=identity.public_key, signature=signature)

    # -- identity / hashing ---------------------------------------------------

    @property
    def signing_payload(self) -> bytes:
        """The exact bytes a valid signature must cover."""
        return _digest(self.nonce, self.public_key)

    @property
    def tx_hash(self) -> bytes:
        """Unique 32-byte identifier of this transaction."""
        h = hashlib.sha256()
        h.update(self.nonce.to_bytes(NONCE_BYTES, "big"))
        h.update(self.public_key)
        h.update(self.signature)
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
        return verify(self.public_key, self.signing_payload, self.signature)

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"Transaction(nonce={self.nonce}, id={self.tx_id[:12]}…)"


def _digest(nonce: int, public_key: bytes) -> bytes:
    """Deterministic digest of the signable content of a transaction."""
    h = hashlib.sha256()
    h.update(_DOMAIN_TAG)
    h.update(nonce.to_bytes(NONCE_BYTES, "big"))
    h.update(public_key)
    return h.digest()
