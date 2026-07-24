"""Tests for the Merkle-backed mempool and the metrics/topology helpers."""

import unittest

from blockchain.core import Transaction
from blockchain.crypto import Identity
from blockchain.metrics import Metrics, aggregate
from blockchain.storage import Mempool
from blockchain.topology import Topology


class TestMempool(unittest.TestCase):
    def setUp(self):
        self.identity = Identity.generate()
        self.mempool = Mempool()

    def test_add_new_returns_true(self):
        tx = Transaction.create(self.identity)
        self.assertTrue(self.mempool.add(tx))
        self.assertEqual(len(self.mempool), 1)

    def test_add_duplicate_returns_false(self):
        tx = Transaction.create(self.identity)
        self.mempool.add(tx)
        self.assertFalse(self.mempool.add(tx))
        self.assertEqual(len(self.mempool), 1)

    def test_contains_and_get(self):
        tx = Transaction.create(self.identity)
        self.mempool.add(tx)
        self.assertIn(tx.tx_hash, self.mempool)
        self.assertEqual(self.mempool.get(tx.tx_hash), tx)

    def test_missing_reports_unknown_hashes(self):
        tx = Transaction.create(self.identity)
        self.mempool.add(tx)
        other = Transaction.create(self.identity, nonce=999).tx_hash
        self.assertEqual(self.mempool.missing([tx.tx_hash, other]), [other])

    def test_root_advances_with_insertions(self):
        r0 = self.mempool.root
        self.mempool.add(Transaction.create(self.identity, nonce=1))
        self.assertNotEqual(self.mempool.root, r0)


class TestMetrics(unittest.TestCase):
    def test_redundancy_ratio(self):
        m = Metrics()
        m.record_transaction(is_new=True)
        m.record_transaction(is_new=False)
        m.record_transaction(is_new=False)
        self.assertAlmostEqual(m.redundancy_ratio, 2 / 3)

    def test_aggregate_over_nodes(self):
        a, b = Metrics(), Metrics()
        a.record_sent("tx", 3)
        b.record_sent("tx", 1)
        agg = aggregate([a, b])
        self.assertEqual(agg["total_packets_sent"], 4)
        self.assertEqual(agg["avg_packets_sent"], 2.0)


class TestTopology(unittest.TestCase):
    def test_undirected_edges_deduped(self):
        topo = Topology.from_adjacency({"a": ["b"], "b": ["a"]})
        self.assertEqual(topo.stats()["edges"], 1)

    def test_degree_stats(self):
        topo = Topology.from_adjacency({"a": ["b", "c"], "b": ["a"], "c": ["a"]})
        stats = topo.stats()
        self.assertEqual(stats["nodes"], 3)
        self.assertEqual(stats["max_degree"], 2)
        self.assertEqual(stats["isolated_nodes"], 0)


if __name__ == "__main__":
    unittest.main()
