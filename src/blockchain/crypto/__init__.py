"""Asymmetric cryptography layer.

Wraps IPv8's ``default_eccrypto`` behind a small, intention-revealing API so the
rest of the codebase never touches the crypto backend directly. Swapping the
backend later only requires changing this package.
"""

from blockchain.crypto.identity import Identity, InvalidSignatureError, verify

__all__ = ["Identity", "verify", "InvalidSignatureError"]
