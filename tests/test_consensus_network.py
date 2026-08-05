"""Consensus over a real IPv8 network, in one process.

The unit tests in ``test_chain`` and ``test_ledger`` hand blocks to a ledger
directly. These start actual peers on loopback and make them agree by talking to
each other, which is the only way to catch the things that live in the wiring:
announcements that are never sent, blocks that are never served, a node that
parks an orphan and never asks for its parent.

They are deliberately small and short. A hundred peers racing at a realistic
difficulty is an experiment (``scripts/run_chain.py``), not a test -- it would be
slow and its timing would make it flaky. Here one miner produces a handful of
easy blocks and everyone else has to end up holding them.
"""

import asyncio
import unittest

from blockchain.consensus import EXTENDED
from blockchain.core import Block, mine
from blockchain.crypto import Identity
from blockchain.execution import Claim, signed
from blockchain.simulation import Simulation, SimulationConfig

#: Easy blocks: this suite is about propagation, not about the cost of a hash.
DIFFICULTY = 8
#: Generous, because it bounds a failure rather than a success: the waits below
#: return the moment the condition holds.
TIMEOUT = 25.0


async def wait_for(predicate, timeout: float = TIMEOUT, interval: float = 0.2) -> bool:
    """Poll until ``predicate()`` holds, or give up. Returns whether it held."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return predicate()


class ConsensusNetworkTest(unittest.IsolatedAsyncioTestCase):
    nodes = 3
    miners = 1

    async def asyncSetUp(self):
        self.sim = Simulation(
            SimulationConfig(
                num_nodes=self.nodes,
                strategy="hybrid",
                strategy_kwargs={"fanout": 3},
                max_peers=self.nodes + 2,
                initial_connections=self.nodes - 1,
                tx_interval=0.0,
                tick_interval=0.5,
                seed=99,
                difficulty=DIFFICULTY,
                mine_interval=0.05,
                hashes_per_round=400,
                announce_interval=0.5,
                miners=self.miners,
            )
        )
        await self.sim.start()
        connected = await wait_for(
            lambda: all(len(c.get_peers()) >= 1 for c in self.sim.communities)
        )
        self.assertTrue(connected, "peers never found each other")

    async def asyncTearDown(self):
        await self.sim.stop()

    # -- helpers --------------------------------------------------------------

    @property
    def heights(self):
        return [c.ledger.height for c in self.sim.communities]

    def agree(self) -> bool:
        return self.sim.chain_agreement()["distinct_heads"] == 1


class TestBlocksPropagate(ConsensusNetworkTest):
    async def test_every_node_ends_up_on_the_miner_s_chain(self):
        grew = await wait_for(lambda: min(self.heights) >= 3)
        self.assertTrue(grew, f"chain did not reach height 3 everywhere: {self.heights}")

        settled = await wait_for(self.agree)
        agreement = self.sim.chain_agreement()
        self.assertTrue(settled, f"nodes disagree on the head: {agreement}")
        self.assertEqual(agreement["distinct_state_roots"], 1, agreement)

    async def test_a_transaction_submitted_anywhere_is_executed_everywhere(self):
        alice = Identity.generate()
        # Submit to a node that does not mine, so the transaction has to travel.
        self.sim.communities[-1].submit(signed(alice, Claim()))

        funded = await wait_for(
            lambda: all(
                c.ledger.state.balance_of(alice.public_key) > 0
                for c in self.sim.communities
            )
        )
        self.assertTrue(
            funded,
            f"balances: {[c.ledger.state.balance_of(alice.public_key) for c in self.sim.communities]}",
        )


class TestLateJoinerSynchronises(ConsensusNetworkTest):
    async def test_a_node_that_starts_late_catches_up(self):
        grew = await wait_for(lambda: min(self.heights) >= 3)
        self.assertTrue(grew, f"chain did not grow: {self.heights}")

        latecomer = await self.sim.join()
        self.assertEqual(latecomer.ledger.height, 0)

        target = max(self.heights)
        caught_up = await wait_for(lambda: latecomer.ledger.height >= target)
        self.assertTrue(
            caught_up,
            f"latecomer stuck at {latecomer.ledger.height}, network at {max(self.heights)}",
        )
        # Same history, and the same world computed from it.
        majority = self.sim.communities[0].ledger
        self.assertTrue(
            await wait_for(lambda: latecomer.ledger.state_root == majority.state_root)
        )


class TestForgedBlocksAreRejected(ConsensusNetworkTest):
    miners = 0  # nobody mines: the only blocks in play are the ones we forge

    async def test_a_block_without_proof_of_work_never_spreads(self):
        victim = self.sim.communities[0]
        genesis = victim.ledger.chain.genesis
        forged = Block.create(1, genesis.block_hash, (), timestamp=1, nonce=7)

        # Straight into the handler, as a hostile peer's packet would arrive.
        victim._connect(forged, source=None)

        await asyncio.sleep(2)
        self.assertEqual(
            [c.ledger.height for c in self.sim.communities], [0] * self.nodes
        )
        self.assertEqual(victim.metrics.blocks_invalid, 1)

    async def test_an_honest_block_from_a_peer_is_accepted(self):
        """The control for the test above: the same path, with real work done."""
        victim = self.sim.communities[0]
        genesis = victim.ledger.chain.genesis
        honest = mine(
            Block.create(1, genesis.block_hash, (), timestamp=1), DIFFICULTY
        )

        self.assertEqual(victim.ledger.connect(honest).status, EXTENDED)
        spread = await wait_for(lambda: min(self.heights) >= 1)
        self.assertTrue(spread, f"honest block did not spread: {self.heights}")


if __name__ == "__main__":
    unittest.main()
