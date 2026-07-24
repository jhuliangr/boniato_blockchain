"""Tests for the Merkle tree."""

import hashlib
import unittest

from blockchain.core import MerkleTree
from blockchain.core.merkle import EMPTY_ROOT, verify_proof


def leaf(n: int) -> bytes:
    return hashlib.sha256(str(n).encode()).digest()


class TestMerkleTree(unittest.TestCase):
    def test_empty_tree_has_well_known_root(self):
        self.assertEqual(MerkleTree().root, EMPTY_ROOT)

    def test_root_changes_when_leaf_added(self):
        tree = MerkleTree()
        r0 = tree.root
        tree.add_leaf(leaf(1))
        self.assertNotEqual(tree.root, r0)

    def test_root_is_order_sensitive(self):
        a = MerkleTree([leaf(1), leaf(2)])
        b = MerkleTree([leaf(2), leaf(1)])
        self.assertNotEqual(a.root, b.root)

    def test_root_is_deterministic(self):
        leaves = [leaf(i) for i in range(5)]
        self.assertEqual(MerkleTree(leaves).root, MerkleTree(leaves).root)

    def test_odd_number_of_leaves(self):
        tree = MerkleTree([leaf(i) for i in range(3)])
        self.assertEqual(len(tree.root), 32)

    def test_membership_proof_valid(self):
        leaves = [leaf(i) for i in range(6)]
        tree = MerkleTree(leaves)
        for i in range(len(leaves)):
            proof = tree.proof(i)
            self.assertTrue(verify_proof(leaves[i], proof, tree.root))

    def test_membership_proof_rejects_wrong_leaf(self):
        leaves = [leaf(i) for i in range(6)]
        tree = MerkleTree(leaves)
        proof = tree.proof(0)
        self.assertFalse(verify_proof(leaf(999), proof, tree.root))


if __name__ == "__main__":
    unittest.main()
