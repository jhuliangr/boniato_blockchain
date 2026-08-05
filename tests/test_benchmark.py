"""Tests for the measurement code itself.

A benchmark that is wrong is worse than no benchmark, because it is quoted. The
cases below are the ones that would silently corrupt a reported number: counting
a transaction that arrived outside the window, reporting zero latency for a
sample that is empty, or crediting a transaction with a confirmation depth the
chain never reached.
"""

import unittest

from blockchain.core import Block, Transaction
from blockchain.crypto import Identity
from blockchain.metrics import ChainBenchmark, PropagationBenchmark, describe


class TestDescribe(unittest.TestCase):
    def test_an_empty_sample_reports_no_data_rather_than_zero(self):
        self.assertEqual(describe([]), {"n": 0})

    def test_median_and_tail(self):
        result = describe([1.0, 2.0, 3.0, 4.0, 100.0])
        self.assertEqual(result["n"], 5)
        self.assertEqual(result["median"], 3.0)
        self.assertEqual(result["max"], 100.0)
        # The mean is dragged by the outlier; the median is not. That is the
        # whole reason both are reported.
        self.assertGreater(result["mean"], result["median"])


class BenchmarkTest(unittest.TestCase):
    def setUp(self):
        self.identity = Identity.generate()
        self.bench = ChainBenchmark()

    def tx(self, nonce: int) -> Transaction:
        return Transaction.create(self.identity, nonce=nonce)

    def block(self, index: int, transactions) -> Block:
        return Block.create(index, b"\x00" * 32, transactions, timestamp=index)


class TestWindowing(BenchmarkTest):
    def test_transactions_outside_the_window_are_not_scored(self):
        warmup_tx, counted_tx = self.tx(1), self.tx(2)

        self.bench.on_submit(warmup_tx.tx_hash, at=0.0)  # before the window opens
        self.bench.start_window(at=1.0)
        self.bench.on_submit(counted_tx.tx_hash, at=1.5)
        self.bench.on_block(self.block(1, (warmup_tx, counted_tx)), height=1, at=2.0)
        self.bench.end_window(at=11.0)

        summary = self.bench.summary()
        self.assertEqual(summary["submitted"], 1)
        self.assertEqual(summary["included"], 1)
        self.assertEqual(summary["inclusion_latency"]["median"], 0.5)

    def test_throughput_is_per_window_second(self):
        self.bench.start_window(at=0.0)
        txs = [self.tx(n) for n in range(4)]
        for tx in txs:
            self.bench.on_submit(tx.tx_hash, at=0.0)
        self.bench.on_block(self.block(1, tuple(txs)), height=1, at=1.0)
        self.bench.end_window(at=2.0)

        self.assertEqual(self.bench.throughput()["tx_per_second"], 2.0)
        self.assertEqual(self.bench.throughput()["blocks_per_second"], 0.5)

    def test_a_transaction_never_mined_counts_as_backlog(self):
        self.bench.start_window(at=0.0)
        self.bench.on_submit(self.tx(1).tx_hash, at=0.5)
        self.bench.end_window(at=10.0)

        summary = self.bench.summary()
        self.assertEqual(summary["unconfirmed"], 1)
        self.assertEqual(summary["inclusion_latency"], {"n": 0})

    def test_resubmitting_does_not_create_a_second_transaction(self):
        self.bench.start_window(at=0.0)
        tx = self.tx(1)
        self.bench.on_submit(tx.tx_hash, at=1.0)
        self.bench.on_submit(tx.tx_hash, at=2.0)  # a peer re-gossips it
        self.assertEqual(self.bench.summary()["submitted"], 1)


class TestConfirmationDepth(BenchmarkTest):
    def setUp(self):
        super().setUp()
        self.tracked = self.tx(1)
        self.bench.start_window(at=0.0)
        self.bench.on_submit(self.tracked.tx_hash, at=0.0)
        self.bench.on_block(self.block(1, (self.tracked,)), height=1, at=1.0)

    def test_depth_one_is_inclusion(self):
        self.assertEqual(self.bench.confirmation_latencies(k=1), [1.0])

    def test_a_depth_the_chain_never_reached_yields_nothing(self):
        self.assertEqual(self.bench.confirmation_latencies(k=3), [])

    def test_latency_grows_with_depth(self):
        self.bench.on_block(self.block(2, ()), height=2, at=5.0)
        self.bench.on_block(self.block(3, ()), height=3, at=9.0)

        self.assertEqual(self.bench.confirmation_latencies(k=2), [5.0])
        self.assertEqual(self.bench.confirmation_latencies(k=3), [9.0])

    def test_a_height_seen_twice_keeps_the_first_time(self):
        """A reorg re-reaches a height; the user was told about it the first time."""
        self.bench.on_block(self.block(2, ()), height=2, at=5.0)
        self.bench.on_block(self.block(2, ()), height=2, at=8.0)
        self.assertEqual(self.bench.confirmation_latencies(k=2), [5.0])


class TestPropagation(unittest.TestCase):
    def test_the_first_sighting_is_the_origin_and_has_no_delay(self):
        prop = PropagationBenchmark()
        prop.on_block(b"block", at=10.0)
        self.assertEqual(prop.summary()["blocks"], 1)
        self.assertEqual(prop.summary()["delay_seconds"], {"n": 0})

    def test_later_sightings_are_measured_from_the_first(self):
        prop = PropagationBenchmark()
        prop.on_block(b"block", at=10.0)
        prop.on_block(b"block", at=10.2)
        prop.on_block(b"block", at=10.4)

        summary = prop.summary()
        self.assertEqual(summary["blocks"], 1)
        self.assertEqual(summary["delay_seconds"]["n"], 2)
        self.assertAlmostEqual(summary["delay_seconds"]["median"], 0.3)


if __name__ == "__main__":
    unittest.main()
