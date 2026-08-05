"""Tests for the block tree, its validation rules and its fork choice.

Everything here runs at a very low difficulty: these tests are about which
branch wins and why, not about how expensive it is to build one.
"""

import unittest
from dataclasses import replace

from blockchain.consensus import (
    DUPLICATE,
    EXTENDED,
    INVALID,
    ORPHAN,
    REORG,
    SIDE,
    Chain,
    check_block,
    work_of,
)
from blockchain.consensus.chain import MAX_ORPHANS
from blockchain.consensus.validation import MAX_BLOCK_TRANSACTIONS
from blockchain.core import Block, Transaction, mine
from blockchain.crypto import Identity

DIFFICULTY = 4


def block_on(parent: Block, transactions=(), timestamp=None, difficulty=DIFFICULTY) -> Block:
    """Mine a valid child of ``parent``."""
    return mine(
        Block.create(
            index=parent.index + 1,
            prev_hash=parent.block_hash,
            transactions=transactions,
            timestamp=parent.timestamp + 1 if timestamp is None else timestamp,
        ),
        difficulty,
    )


class TestValidation(unittest.TestCase):
    def setUp(self):
        self.identity = Identity.generate()
        self.genesis = Block.genesis()

    def test_a_well_formed_block_passes(self):
        self.assertIsNone(
            check_block(block_on(self.genesis), difficulty=DIFFICULTY, parent=self.genesis)
        )

    def test_block_without_proof_of_work_is_rejected(self):
        unmined = Block.create(1, self.genesis.block_hash, (), timestamp=1, nonce=0)
        reason = check_block(unmined, difficulty=32, parent=self.genesis)
        self.assertIn("proof-of-work", reason)

    def test_merkle_root_must_commit_to_the_transactions(self):
        honest = block_on(self.genesis)
        forged = replace(honest, transactions=(Transaction.create(self.identity),))
        reason = check_block(forged, difficulty=DIFFICULTY, parent=self.genesis)
        self.assertIn("merkle", reason)

    def test_a_bad_signature_anywhere_rejects_the_block(self):
        good = Transaction.create(self.identity)
        forged = replace(good, signature=b"\x00" * len(good.signature))
        block = block_on(self.genesis, transactions=(forged,))
        reason = check_block(block, difficulty=DIFFICULTY, parent=self.genesis)
        self.assertIn("signature", reason)

    def test_the_same_transaction_twice_in_one_block_is_rejected(self):
        tx = Transaction.create(self.identity)
        block = block_on(self.genesis, transactions=(tx, tx))
        reason = check_block(block, difficulty=DIFFICULTY, parent=self.genesis)
        self.assertIn("duplicate", reason)

    def test_too_many_transactions_is_rejected(self):
        txs = tuple(
            Transaction.create(self.identity, nonce=n)
            for n in range(MAX_BLOCK_TRANSACTIONS + 1)
        )
        block = block_on(self.genesis, transactions=txs)
        reason = check_block(block, difficulty=DIFFICULTY, parent=self.genesis)
        self.assertIn("too many", reason)

    def test_height_must_follow_the_parent(self):
        block = mine(
            Block.create(5, self.genesis.block_hash, (), timestamp=1), DIFFICULTY
        )
        reason = check_block(block, difficulty=DIFFICULTY, parent=self.genesis)
        self.assertIn("does not follow", reason)

    def test_timestamp_may_not_go_backwards(self):
        parent = block_on(self.genesis, timestamp=1000)
        child = block_on(parent, timestamp=999)
        reason = check_block(child, difficulty=DIFFICULTY, parent=parent)
        self.assertIn("timestamp", reason)


class TestChainGrowth(unittest.TestCase):
    def setUp(self):
        self.genesis = Block.genesis()
        self.chain = Chain(self.genesis, DIFFICULTY)

    def test_starts_at_genesis(self):
        self.assertEqual(self.chain.height, 0)
        self.assertEqual(self.chain.head, self.genesis)
        self.assertEqual(self.chain.total_work, work_of(DIFFICULTY))

    def test_a_valid_child_extends_the_chain(self):
        update = self.chain.add(block_on(self.genesis))
        self.assertEqual(update.status, EXTENDED)
        self.assertEqual(self.chain.height, 1)
        self.assertEqual(update.reverted, ())
        self.assertEqual(len(update.applied), 1)

    def test_the_same_block_twice_changes_nothing(self):
        block = block_on(self.genesis)
        self.chain.add(block)
        update = self.chain.add(block)
        self.assertEqual(update.status, DUPLICATE)
        self.assertEqual(self.chain.height, 1)

    def test_an_invalid_block_is_not_kept(self):
        unmined = Block.create(1, self.genesis.block_hash, (), timestamp=1, nonce=0)
        chain = Chain(self.genesis, difficulty=32)
        update = chain.add(unmined)
        self.assertEqual(update.status, INVALID)
        self.assertNotIn(unmined.block_hash, chain)

    def test_work_accumulates_with_height(self):
        parent = self.genesis
        for _ in range(3):
            parent = block_on(parent)
            self.chain.add(parent)
        self.assertEqual(self.chain.total_work, 4 * work_of(DIFFICULTY))


class TestForkChoice(unittest.TestCase):
    def setUp(self):
        self.genesis = Block.genesis()
        self.chain = Chain(self.genesis, DIFFICULTY)
        # Two different children of genesis: a fork by construction.
        self.left = block_on(self.genesis, timestamp=10)
        self.right = block_on(self.genesis, timestamp=20)
        self.chain.add(self.left)

    def test_an_equal_branch_does_not_displace_the_incumbent(self):
        update = self.chain.add(self.right)
        self.assertEqual(update.status, SIDE)
        self.assertEqual(self.chain.head, self.left)
        self.assertEqual(self.chain.branch_count(), 2)

    def test_a_heavier_branch_wins_and_reports_the_swap(self):
        self.chain.add(self.right)
        update = self.chain.add(block_on(self.right))

        self.assertEqual(update.status, REORG)
        self.assertEqual(update.depth, 1)
        self.assertEqual(update.reverted, (self.left,))
        self.assertEqual(self.chain.height, 2)

    def test_a_side_branch_is_kept_and_can_win_later(self):
        self.chain.add(self.right)
        self.assertIn(self.right.block_hash, self.chain)
        self.assertFalse(self.chain.is_active(self.right.block_hash))

        self.chain.add(block_on(self.right))
        self.assertTrue(self.chain.is_active(self.right.block_hash))
        self.assertFalse(self.chain.is_active(self.left.block_hash))

    def test_reorg_reports_both_branches_oldest_first(self):
        deep_left = block_on(self.left)
        self.chain.add(deep_left)  # head is now left/2

        right_1 = block_on(self.right)
        right_2 = block_on(right_1)
        self.chain.add(self.right)
        self.chain.add(right_1)
        update = self.chain.add(right_2)

        self.assertEqual(update.status, REORG)
        self.assertEqual(update.reverted, (self.left, deep_left))
        self.assertEqual(update.applied, (self.right, right_1, right_2))


class TestOrphans(unittest.TestCase):
    def setUp(self):
        self.genesis = Block.genesis()
        self.chain = Chain(self.genesis, DIFFICULTY)

    def test_a_block_without_its_parent_is_parked(self):
        parent = block_on(self.genesis)
        update = self.chain.add(block_on(parent))
        self.assertEqual(update.status, ORPHAN)
        self.assertEqual(self.chain.height, 0)
        self.assertEqual(self.chain.orphan_count, 1)

    def test_the_missing_parent_connects_the_whole_run(self):
        first = block_on(self.genesis)
        second = block_on(first)
        third = block_on(second)

        self.chain.add(third)
        self.chain.add(second)
        self.assertEqual(self.chain.height, 0)

        update = self.chain.add(first)
        self.assertEqual(update.status, EXTENDED)
        self.assertEqual(self.chain.height, 3)
        self.assertEqual(update.applied, (first, second, third))
        self.assertEqual(self.chain.orphan_count, 0)

    def test_the_orphan_pool_is_bounded(self):
        # Every one of these claims a parent nobody has, so none can connect.
        unknown_parent = block_on(self.genesis)
        for i in range(MAX_ORPHANS + 20):
            self.chain.add(block_on(unknown_parent, timestamp=1000 + i))
        self.assertLessEqual(self.chain.orphan_count, MAX_ORPHANS)


class TestReplayProtection(unittest.TestCase):
    def setUp(self):
        self.identity = Identity.generate()
        self.genesis = Block.genesis()
        self.chain = Chain(self.genesis, DIFFICULTY)
        self.tx = Transaction.create(self.identity)
        self.first = block_on(self.genesis, transactions=(self.tx,))
        self.chain.add(self.first)

    def test_a_transaction_cannot_be_mined_twice_on_one_branch(self):
        update = self.chain.add(block_on(self.first, transactions=(self.tx,)))
        self.assertEqual(update.status, INVALID)
        self.assertIn("replays", update.reason)

    def test_the_same_transaction_is_fine_on_a_competing_branch(self):
        # A transaction spent on the branch we follow was never spent on another.
        sibling = block_on(self.genesis, transactions=(self.tx,), timestamp=500)
        update = self.chain.add(sibling)
        self.assertEqual(update.status, SIDE)

    def test_an_unrelated_transaction_is_unaffected(self):
        other = Transaction.create(self.identity, nonce=999)
        update = self.chain.add(block_on(self.first, transactions=(other,)))
        self.assertEqual(update.status, EXTENDED)


if __name__ == "__main__":
    unittest.main()
