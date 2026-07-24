"""Tests for the Transaction domain model."""

import unittest
from dataclasses import replace

from blockchain.core import Transaction
from blockchain.crypto import Identity


class TestTransaction(unittest.TestCase):
    def setUp(self):
        self.identity = Identity.generate()

    def test_created_transaction_is_valid(self):
        tx = Transaction.create(self.identity)
        self.assertTrue(tx.is_valid())

    def test_public_key_matches_signer(self):
        tx = Transaction.create(self.identity)
        self.assertEqual(tx.public_key, self.identity.public_key)

    def test_tampered_nonce_invalidates(self):
        tx = Transaction.create(self.identity, nonce=1)
        forged = replace(tx, nonce=2)  # signature no longer matches
        self.assertFalse(forged.is_valid())

    def test_forged_signature_invalidates(self):
        tx = Transaction.create(self.identity)
        forged = replace(tx, signature=b"\x00" * len(tx.signature))
        self.assertFalse(forged.is_valid())

    def test_negative_nonce_rejected_without_crashing(self):
        tx = Transaction.create(self.identity, nonce=1)
        forged = replace(tx, nonce=-5)
        self.assertFalse(forged.is_valid())

    def test_hash_is_deterministic_and_32_bytes(self):
        tx = Transaction.create(self.identity, nonce=42)
        self.assertEqual(tx.tx_hash, tx.tx_hash)
        self.assertEqual(len(tx.tx_hash), 32)

    def test_distinct_nonces_give_distinct_hashes(self):
        a = Transaction.create(self.identity, nonce=1)
        b = Transaction.create(self.identity, nonce=2)
        self.assertNotEqual(a.tx_hash, b.tx_hash)


if __name__ == "__main__":
    unittest.main()
