"""Tests for the action instruction set and its wire codec."""

import unittest

from blockchain.core.transaction import MAX_ACTION_BYTES
from blockchain.crypto import Identity
from blockchain.execution.actions import (
    MAX_AMOUNT,
    MAX_LAND_ID,
    BuyLand,
    Claim,
    Fertilize,
    Harvest,
    Plant,
    Transfer,
    decode,
    signed,
)


class TestActionCodec(unittest.TestCase):
    def setUp(self):
        self.recipient = b"\x11" * 74

    def _round_trip(self, action):
        blob = action.encode()
        self.assertLessEqual(len(blob), MAX_ACTION_BYTES)
        self.assertEqual(decode(blob), action)

    def test_every_action_round_trips(self):
        for action in (
            Claim(),
            BuyLand(),
            Plant(land_id=0),
            Plant(land_id=MAX_LAND_ID),
            Harvest(land_id=7),
            Transfer(recipient=self.recipient, amount=1),
            Transfer(recipient=self.recipient, amount=MAX_AMOUNT),
            Fertilize(land_id=0, amount=0),
            Fertilize(land_id=MAX_LAND_ID, amount=MAX_AMOUNT),
        ):
            with self.subTest(action=action):
                self._round_trip(action)

    def test_distinct_actions_have_distinct_tags(self):
        tags = {
            a.encode()[0]
            for a in (Claim(), BuyLand(), Plant(0), Harvest(0), Transfer(b"k", 1), Fertilize(0, 1))
        }
        self.assertEqual(len(tags), 6)

    def test_plant_and_harvest_of_same_plot_differ(self):
        self.assertNotEqual(Plant(land_id=3).encode(), Harvest(land_id=3).encode())


class TestDecodeIsTotal(unittest.TestCase):
    """Decoding is fed straight from the network, so nothing may raise."""

    def test_malformed_blobs_return_none(self):
        blobs = [
            b"",  # empty
            b"\x00",  # unknown tag
            b"\xff\xff\xff",  # unknown tag with body
            b"\x01\x00",  # Claim with trailing junk
            b"\x03\x00",  # BuyLand with trailing junk
            b"\x04",  # Plant with no land id
            b"\x04\x00\x00",  # Plant with a truncated land id
            b"\x04\x00\x00\x00\x00\x00",  # Plant with an over-long land id
            b"\x05\x00",  # Harvest with a truncated land id
            b"\x02",  # Transfer with no body
            b"\x02" + b"\x00" * 9,  # Transfer with a truncated header
            b"\x02" + b"\x00" * 8 + b"\x00\x05" + b"ab",  # key shorter than key_len
            b"\x02" + b"\x00" * 8 + b"\x00\x02" + b"abcd",  # key longer than key_len
            b"\x02" + b"\x00" * 8 + b"\x00\x00",  # zero-length recipient
            b"\x06",  # Fertilize with no body
            b"\x06" + b"\x00" * 11,  # Fertilize with a truncated amount
            b"\x06" + b"\x00" * 13,  # Fertilize with trailing junk
            b"\x01" * (MAX_ACTION_BYTES + 1),  # oversized
        ]
        for blob in blobs:
            with self.subTest(blob=blob):
                self.assertIsNone(decode(blob))

    def test_every_leading_byte_decodes_or_returns_none(self):
        """Sweep the whole tag space: each byte either parses or is rejected."""
        known = (Claim, BuyLand, Plant, Harvest, Transfer, Fertilize)
        for tag in range(256):
            for body in (b"", b"\x00", b"\x00" * 12, b"\x00" * 74):
                result = decode(bytes([tag]) + body)
                if result is not None:
                    self.assertIsInstance(result, known)


class TestEncodeRejectsOutOfRange(unittest.TestCase):
    """Encoding is local, so an impossible value should fail loudly."""

    def test_land_id_too_large(self):
        with self.assertRaises(ValueError):
            Plant(land_id=MAX_LAND_ID + 1).encode()

    def test_negative_land_id(self):
        with self.assertRaises(ValueError):
            Harvest(land_id=-1).encode()

    def test_amount_too_large(self):
        with self.assertRaises(ValueError):
            Transfer(recipient=b"k", amount=MAX_AMOUNT + 1).encode()
        with self.assertRaises(ValueError):
            Fertilize(land_id=0, amount=MAX_AMOUNT + 1).encode()

    def test_empty_recipient(self):
        with self.assertRaises(ValueError):
            Transfer(recipient=b"", amount=1).encode()


class TestSigning(unittest.TestCase):
    def setUp(self):
        self.identity = Identity.generate()

    def test_signed_transaction_verifies_and_carries_the_action(self):
        action = Plant(land_id=5)
        tx = signed(self.identity, action)
        self.assertTrue(tx.is_valid())
        self.assertEqual(decode(tx.action), action)

    def test_signature_covers_the_action(self):
        """Swapping the action must invalidate the signature.

        Without this, a relaying peer could turn somebody's Plant into a
        Transfer of their whole balance.
        """
        from dataclasses import replace

        tx = signed(self.identity, Plant(land_id=5), nonce=1)
        forged = replace(tx, action=Harvest(land_id=5).encode())
        self.assertFalse(forged.is_valid())

    def test_same_nonce_different_action_gives_different_hash(self):
        a = signed(self.identity, Plant(land_id=1), nonce=9)
        b = signed(self.identity, Harvest(land_id=1), nonce=9)
        self.assertNotEqual(a.tx_hash, b.tx_hash)

    def test_dummy_transaction_still_has_an_empty_action(self):
        """The phase-2 transaction shape must survive the new field."""
        from blockchain.core import Transaction

        tx = Transaction.create(self.identity)
        self.assertEqual(tx.action, b"")
        self.assertTrue(tx.is_valid())

    def test_oversized_action_is_refused_at_creation(self):
        from blockchain.core import Transaction

        with self.assertRaises(ValueError):
            Transaction.create(self.identity, action=b"\x01" * (MAX_ACTION_BYTES + 1))


if __name__ == "__main__":
    unittest.main()
