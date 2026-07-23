"""Cryptographic identity: key management, signing and verification.

This module is the single place that knows about the concrete crypto backend
(IPv8's ``default_eccrypto``, elliptic-curve keys). Everything else in the
project speaks in terms of :class:`Identity` and raw ``bytes`` public keys /
signatures.

Answers to the Phase-1 questions live in ``docs/phase1-ipv8.md``; the code here
is the practical counterpart:

- *How are messages signed?*  -> :meth:`Identity.sign`
- *How is a signature verified?* -> :func:`verify`
- *How is a peer identified?* -> its public key (see :attr:`Identity.public_key`).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ipv8.keyvault.crypto import default_eccrypto

# Security level understood by IPv8's key generator. "medium" matches the keys
# produced by the course's ``ipv8_test.py`` example (ec1.pem / ec2.pem).
DEFAULT_SECURITY_LEVEL = "medium"


class InvalidSignatureError(Exception):
    """Raised when a signature does not match the data / public key."""


class Identity:
    """A private/public key pair used to sign transactions.

    An :class:`Identity` owns a private key and can therefore *sign*. To only
    *verify* data coming from others, use the module-level :func:`verify`
    function with the sender's public-key bytes no private material needed.
    """

    def __init__(self, private_key) -> None:
        # ``private_key`` is an IPv8 PrivateKey instance. Kept private (``_``)
        # so callers cannot leak it accidentally.
        self._private_key = private_key

    # -- construction ---------------------------------------------------------

    @classmethod
    def generate(cls, security_level: str = DEFAULT_SECURITY_LEVEL) -> "Identity":
        """Create a brand-new random identity."""
        return cls(default_eccrypto.generate_key(security_level))

    @classmethod
    def from_file(cls, path: str | Path) -> "Identity":
        """Load an identity from a ``.pem`` file, creating it if missing.

        This mirrors how IPv8's ``ConfigBuilder.add_key`` persists keys, so the
        same ``ecN.pem`` files can be shared between this project and plain IPv8.
        """
        path = Path(path)
        if path.exists():
            key = default_eccrypto.key_from_private_bin(path.read_bytes())
            return cls(key)
        identity = cls.generate()
        path.write_bytes(identity._private_key.key_to_bin())
        return identity

    # -- properties -----------------------------------------------------------

    @property
    def public_key(self) -> bytes:
        """Serialized public key the peer's network-wide identifier."""
        return self._private_key.pub().key_to_bin()

    @property
    def address(self) -> str:
        """Short, human-friendly fingerprint (SHA-1 of the public key, hex).

        Equivalent in spirit to IPv8's peer ``mid``; handy for logs and graphs.
        """
        return hashlib.sha1(self.public_key).hexdigest()

    # -- operations -----------------------------------------------------------

    def sign(self, data: bytes) -> bytes:
        """Produce a digital signature over ``data`` with the private key."""
        return default_eccrypto.create_signature(self._private_key, data)


def verify(public_key: bytes, data: bytes, signature: bytes) -> bool:
    """Return ``True`` iff ``signature`` is a valid signature of ``data``.

    ``public_key`` is the serialized key bytes as produced by
    :attr:`Identity.public_key`. A malformed key returns ``False`` rather than
    raising, so it is safe to feed untrusted network input straight in.
    """
    try:
        key = default_eccrypto.key_from_public_bin(public_key)
    except Exception:
        return False
    return default_eccrypto.is_valid_signature(key, data, signature)
