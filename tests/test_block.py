"""Tests for the Block domain model and the Proof-of-Work mechanism."""

import unittest
from dataclasses import replace

from blockchain.core import (
    Block,
    GENESIS_PREV_HASH,
    Transaction,
    has_proof_of_work,
    leading_zero_bits,
    mine,
)
from blockchain.core.block import merkle_root_of
from blockchain.crypto import Identity


class TestBlock(unittest.TestCase):
    def setUp(self):
        self.identity = Identity.generate()
        self.txs = tuple(Transaction.create(self.identity, nonce=n) for n in range(3))

    def test_genesis_has_no_predecessor_and_empty_txs(self):
        g = Block.genesis()
        self.assertEqual(g.index, 0)
        self.assertEqual(g.prev_hash, GENESIS_PREV_HASH)
        self.assertEqual(g.transactions, ())

    def test_create_commits_transactions_via_merkle_root(self):
        block = Block.create(1, GENESIS_PREV_HASH, self.txs, timestamp=1000)
        self.assertEqual(block.merkle_root, merkle_root_of(self.txs))
        self.assertTrue(block.has_consistent_merkle_root())

    def test_block_hash_is_deterministic_and_32_bytes(self):
        block = Block.create(1, GENESIS_PREV_HASH, self.txs, timestamp=1000)
        self.assertEqual(block.block_hash, block.block_hash)
        self.assertEqual(len(block.block_hash), 32)

    def test_changing_nonce_changes_hash_but_not_txs(self):
        block = Block.create(1, GENESIS_PREV_HASH, self.txs, timestamp=1000)
        rehashed = block.with_nonce(999)
        self.assertNotEqual(block.block_hash, rehashed.block_hash)
        self.assertEqual(block.transactions, rehashed.transactions)
        self.assertEqual(block.merkle_root, rehashed.merkle_root)

    def test_valid_block_passes_self_check(self):
        block = Block.create(1, GENESIS_PREV_HASH, self.txs, timestamp=1000)
        self.assertTrue(block.is_valid())

    def test_tampered_transaction_set_breaks_merkle_root(self):
        block = Block.create(1, GENESIS_PREV_HASH, self.txs, timestamp=1000)
        extra = Transaction.create(self.identity, nonce=99)
        forged = replace(block, transactions=block.transactions + (extra,))
        self.assertFalse(forged.has_consistent_merkle_root())
        self.assertFalse(forged.is_valid())

    def test_block_with_invalid_transaction_is_invalid(self):
        bad_tx = replace(self.txs[0], nonce=self.txs[0].nonce + 1)  # signature breaks
        block = Block.create(1, GENESIS_PREV_HASH, (bad_tx,), timestamp=1000)
        self.assertFalse(block.is_valid())


class TestProofOfWork(unittest.TestCase):
    def setUp(self):
        self.block = Block.genesis()

    def test_leading_zero_bits_counts_correctly(self):
        self.assertEqual(leading_zero_bits(b"\x00\x00\xff"), 16)
        self.assertEqual(leading_zero_bits(b"\x0f\xff"), 4)
        self.assertEqual(leading_zero_bits(b"\xff"), 0)
        self.assertEqual(leading_zero_bits(b"\x00\x00\x00"), 24)

    def test_mining_produces_valid_proof_of_work(self):
        difficulty = 10  # ~1024 hashes, fast
        mined = mine(self.block, difficulty)
        self.assertTrue(has_proof_of_work(mined, difficulty))
        self.assertGreaterEqual(leading_zero_bits(mined.block_hash), difficulty)

    def test_mining_preserves_block_contents(self):
        mined = mine(self.block, 8)
        self.assertEqual(mined.transactions, self.block.transactions)
        self.assertEqual(mined.merkle_root, self.block.merkle_root)
        self.assertEqual(mined.prev_hash, self.block.prev_hash)

    def test_unmined_block_usually_fails_the_threshold(self):
        # An arbitrary nonce is astronomically unlikely to clear 24 zero bits.
        self.assertFalse(has_proof_of_work(self.block.with_nonce(0), 24))

    def test_zero_difficulty_is_always_satisfied(self):
        self.assertTrue(has_proof_of_work(self.block, 0))

    def test_negative_difficulty_rejected(self):
        with self.assertRaises(ValueError):
            mine(self.block, -1)


if __name__ == "__main__":
    unittest.main()
