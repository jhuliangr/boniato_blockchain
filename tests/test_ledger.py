"""Tests for consensus and execution held together.

The question these ask is not "does the chain pick the right branch" -- that is
``test_chain.py`` -- but "does the world state follow it when it does". A node
that reorganises its chain and keeps the old state would report balances that
never existed, which is worse than not reorganising at all.
"""

import unittest

from blockchain.consensus import EXTENDED, REORG, SIDE, Ledger, Miner
from blockchain.core import Block, Transaction, mine
from blockchain.crypto import Identity
from blockchain.execution import BONI, Claim, Plant, Transfer, signed

DIFFICULTY = 4


def block_on(parent: Block, transactions=(), timestamp=None) -> Block:
    return mine(
        Block.create(
            index=parent.index + 1,
            prev_hash=parent.block_hash,
            transactions=transactions,
            timestamp=parent.timestamp + 1 if timestamp is None else timestamp,
        ),
        DIFFICULTY,
    )


class TestExecutionFollowsTheChain(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger(difficulty=DIFFICULTY)
        self.alice = Identity.generate()
        self.genesis = self.ledger.chain.genesis

    def test_connecting_a_block_executes_it(self):
        claim = signed(self.alice, Claim())
        update = self.ledger.connect(block_on(self.genesis, transactions=(claim,)))

        self.assertEqual(update.status, EXTENDED)
        self.assertEqual(len(update.applied), 1)
        receipt = update.applied[0].receipts[0]
        self.assertTrue(receipt.ok)
        self.assertGreater(self.ledger.state.balance_of(self.alice.public_key), 0)

    def test_a_side_branch_does_not_touch_the_state(self):
        self.ledger.connect(block_on(self.genesis, timestamp=10))
        root_before = self.ledger.state_root

        claim = signed(self.alice, Claim())
        update = self.ledger.connect(
            block_on(self.genesis, transactions=(claim,), timestamp=20)
        )

        self.assertEqual(update.status, SIDE)
        self.assertEqual(self.ledger.state_root, root_before)
        self.assertEqual(self.ledger.state.balance_of(self.alice.public_key), 0)

    def test_a_reorg_rebuilds_the_state_from_the_winning_branch(self):
        claim = signed(self.alice, Claim())
        losing = block_on(self.genesis, transactions=(claim,), timestamp=10)
        self.ledger.connect(losing)
        self.assertGreater(self.ledger.state.balance_of(self.alice.public_key), 0)

        # A heavier branch that never contained the claim.
        rival = block_on(self.genesis, timestamp=20)
        self.ledger.connect(rival)
        update = self.ledger.connect(block_on(rival))

        self.assertEqual(update.status, REORG)
        self.assertEqual(self.ledger.state.balance_of(self.alice.public_key), 0)
        self.assertEqual(self.ledger.rebuild_cost()["reorgs"], 1)

    def test_state_root_is_reproducible_on_a_second_node(self):
        """The property consensus depends on: same blocks, same world."""
        parent = self.genesis
        for nonce in range(3):
            tx = signed(self.alice, Claim()) if nonce == 0 else Transaction.create(
                self.alice, nonce=nonce
            )
            parent = block_on(parent, transactions=(tx,))
            self.ledger.connect(parent)

        replica = Ledger(difficulty=DIFFICULTY)
        for block in self.ledger.active_chain()[1:]:
            replica.connect(block)

        self.assertEqual(replica.state_root, self.ledger.state_root)
        self.assertEqual(replica.chain.head_hash, self.ledger.chain.head_hash)


class TestMempoolFollowsTheHead(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger(difficulty=DIFFICULTY)
        self.alice = Identity.generate()
        self.genesis = self.ledger.chain.genesis

    def test_a_mined_transaction_leaves_the_queue(self):
        tx = signed(self.alice, Claim())
        self.ledger.submit(tx)
        self.assertEqual(len(self.ledger.pending), 1)

        self.ledger.connect(block_on(self.genesis, transactions=(tx,)))
        self.assertEqual(len(self.ledger.pending), 0)

    def test_a_reverted_transaction_comes_back(self):
        tx = signed(self.alice, Claim())
        self.ledger.submit(tx)
        losing = block_on(self.genesis, transactions=(tx,), timestamp=10)
        self.ledger.connect(losing)
        self.assertEqual(len(self.ledger.pending), 0)

        rival = block_on(self.genesis, timestamp=20)
        self.ledger.connect(rival)
        update = self.ledger.connect(block_on(rival))

        self.assertEqual(update.returned, (tx,))
        self.assertEqual(self.ledger.pending, (tx,))

    def test_a_transaction_on_both_branches_does_not_come_back(self):
        tx = signed(self.alice, Claim())
        self.ledger.submit(tx)
        losing = block_on(self.genesis, transactions=(tx,), timestamp=10)
        self.ledger.connect(losing)

        rival = block_on(self.genesis, timestamp=20)
        self.ledger.connect(rival)
        # The winning branch mines the same transaction at a different height.
        self.ledger.connect(block_on(rival, transactions=(tx,)))

        self.assertEqual(self.ledger.pending, ())

    def test_an_unsigned_transaction_is_refused(self):
        from dataclasses import replace

        good = signed(self.alice, Claim())
        forged = replace(good, signature=b"\x00" * len(good.signature))
        self.assertFalse(self.ledger.submit(forged))
        self.assertEqual(len(self.ledger.pending), 0)

    def test_the_same_transaction_is_only_queued_once(self):
        tx = signed(self.alice, Claim())
        self.assertTrue(self.ledger.submit(tx))
        self.assertFalse(self.ledger.submit(tx))
        self.assertEqual(len(self.ledger.pending), 1)


class TestMiner(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger(difficulty=DIFFICULTY)
        self.alice = Identity.generate()

    def mine_one(self, miner: Miner) -> Block:
        for _ in range(10_000):
            block = miner.step()
            if block is not None:
                return block
        self.fail("miner found nothing in 10,000 rounds")

    def test_it_finds_a_block_that_the_chain_accepts(self):
        miner = Miner(self.ledger, hashes_per_round=50, clock=lambda: 100)
        block = self.mine_one(miner)
        self.assertEqual(self.ledger.connect(block).status, EXTENDED)

    def test_it_includes_pending_transactions(self):
        self.ledger.submit(signed(self.alice, Claim()))
        miner = Miner(self.ledger, hashes_per_round=50, clock=lambda: 100)
        block = self.mine_one(miner)
        self.assertEqual(len(block.transactions), 1)

    def test_the_nonce_survives_a_rebuilt_template(self):
        """Work in progress is not lost when the template changes.

        Resetting the counter on every rebuild would make a miner re-test the
        same low nonces forever whenever transactions arrive faster than blocks
        are found -- and never complete a search. Run against a difficulty no
        round will ever satisfy, so the search is still in progress at the end.
        """
        unreachable = Ledger(difficulty=64)
        self.ledger = unreachable
        miner = Miner(unreachable, hashes_per_round=5, clock=lambda: 100)
        miner.step()
        self.ledger.submit(signed(self.alice, Claim()))  # forces a new template
        miner.step()
        self.assertGreaterEqual(miner.hashes_tried, 10)
        self.assertEqual(len(miner.candidate.transactions), 1)

    def test_it_abandons_a_template_built_on_a_stale_head(self):
        # Difficulty nothing will ever meet: the miner keeps searching, so the
        # template it holds is the thing under test rather than a lucky hit.
        ledger = Ledger(difficulty=64)
        miner = Miner(ledger, hashes_per_round=5, clock=lambda: 100)
        miner.step()
        stale = miner.candidate
        ledger.chain.difficulty = DIFFICULTY  # let a real block in
        ledger.connect(block_on(ledger.chain.genesis))
        ledger.chain.difficulty = 64
        miner.step()
        self.assertNotEqual(miner.candidate.prev_hash, stale.prev_hash)
        self.assertEqual(miner.candidate.prev_hash, ledger.chain.head_hash)


class TestGameOverConsensus(unittest.TestCase):
    """A reorg has to undo game state, not just balances."""

    def setUp(self):
        self.ledger = Ledger(difficulty=DIFFICULTY)
        self.alice = Identity.generate()
        self.bob = Identity.generate()
        self.genesis = self.ledger.chain.genesis

    def test_a_transfer_is_undone_when_its_block_loses(self):
        claim = signed(self.alice, Claim())
        first = block_on(self.genesis, transactions=(claim,), timestamp=10)
        self.ledger.connect(first)
        funded = self.ledger.state.balance_of(self.alice.public_key)

        transfer = signed(self.alice, Transfer(recipient=self.bob.public_key, amount=5 * BONI))
        self.ledger.connect(block_on(first, transactions=(transfer,)))
        self.assertEqual(self.ledger.state.balance_of(self.bob.public_key), 5 * BONI)

        # A heavier branch that keeps the claim but not the transfer.
        rival = block_on(first, timestamp=500)
        self.ledger.connect(rival)
        self.ledger.connect(block_on(rival))

        self.assertEqual(self.ledger.state.balance_of(self.bob.public_key), 0)
        self.assertEqual(self.ledger.state.balance_of(self.alice.public_key), funded)

    def test_a_planted_crop_is_unplanted_when_its_block_loses(self):
        claim = signed(self.alice, Claim())
        first = block_on(self.genesis, transactions=(claim,), timestamp=10)
        self.ledger.connect(first)
        land_id = self.ledger.state.lands_of(self.alice.public_key)[0].land_id

        plant = signed(self.alice, Plant(land_id=land_id))
        planted = block_on(first, transactions=(plant,))
        self.ledger.connect(planted)
        self.assertTrue(self.ledger.state.farmlands[land_id].is_planted)

        rival = block_on(first, timestamp=500)
        self.ledger.connect(rival)
        self.ledger.connect(block_on(rival))

        self.assertFalse(self.ledger.state.farmlands[land_id].is_planted)


if __name__ == "__main__":
    unittest.main()
