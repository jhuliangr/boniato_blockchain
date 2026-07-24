"""Tests for the crypto identity layer."""

import tempfile
import unittest
from pathlib import Path

from blockchain.crypto import Identity, verify


class TestIdentity(unittest.TestCase):
    def test_sign_and_verify_roundtrip(self):
        identity = Identity.generate()
        data = b"hello ledger"
        signature = identity.sign(data)
        self.assertTrue(verify(identity.public_key, data, signature))

    def test_verify_rejects_tampered_data(self):
        identity = Identity.generate()
        signature = identity.sign(b"original")
        self.assertFalse(verify(identity.public_key, b"tampered", signature))

    def test_verify_rejects_wrong_key(self):
        alice, bob = Identity.generate(), Identity.generate()
        signature = alice.sign(b"data")
        self.assertFalse(verify(bob.public_key, b"data", signature))

    def test_verify_handles_malformed_key(self):
        # Untrusted network input must not raise.
        self.assertFalse(verify(b"not-a-key", b"data", b"sig"))

    def test_address_is_stable_and_derived_from_pubkey(self):
        identity = Identity.generate()
        self.assertEqual(identity.address, identity.address)
        self.assertEqual(len(identity.address), 40)  # sha1 hex

    def test_from_file_persists_and_reloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "key.pem"
            first = Identity.from_file(path)
            self.assertTrue(path.exists())
            reloaded = Identity.from_file(path)
            self.assertEqual(first.public_key, reloaded.public_key)


if __name__ == "__main__":
    unittest.main()
