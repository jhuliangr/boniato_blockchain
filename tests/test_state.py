"""Tests for the world state, the perishable larder and the commitment hash."""

import unittest

from blockchain.execution.economy import BONI, BP, Economy
from blockchain.execution.state import Farmland, Larder, Lot, WorldState

ALICE = b"\xa1" * 74
BOB = b"\xb0" * 74

#: A far-off expiry, for tests that do not care about spoilage.
NEVER = 10**9


class TestLarder(unittest.TestCase):
    """The perishable balance. Order is the invariant that matters."""

    def test_starts_empty(self):
        larder = Larder()
        self.assertEqual(larder.total(), 0)
        self.assertFalse(larder)
        self.assertEqual(larder.lots, ())

    def test_lots_are_ordered_soonest_to_rot_first(self):
        larder = Larder()
        for expiry in (300, 100, 200):
            larder.add(1 * BONI, expiry)
        self.assertEqual([lot.expires_at for lot in larder.lots], [100, 200, 300])

    def test_lots_sharing_an_expiry_are_merged(self):
        """Keeps the structure canonical, so the state hash agrees across nodes."""
        larder = Larder()
        larder.add(3 * BONI, 100)
        larder.add(2 * BONI, 100)
        self.assertEqual(len(larder), 1)
        self.assertEqual(larder.total(), 5 * BONI)

    def test_two_larders_built_in_different_orders_are_equal(self):
        forwards, backwards = Larder(), Larder()
        for expiry in (100, 200, 300):
            forwards.add(1 * BONI, expiry)
        for expiry in (300, 100, 200):
            backwards.add(1 * BONI, expiry)
        self.assertEqual(forwards, backwards)

    def test_zero_deposits_are_ignored(self):
        larder = Larder()
        larder.add(0, 100)
        self.assertEqual(len(larder), 0)

    def test_negative_deposits_raise(self):
        with self.assertRaises(ValueError):
            Larder().add(-1, 100)

    # -- taking ---------------------------------------------------------------

    def test_take_consumes_the_oldest_lot_first(self):
        larder = Larder([Lot(5 * BONI, 100), Lot(5 * BONI, 200)])
        taken = larder.take(3 * BONI)
        self.assertEqual(taken, (Lot(3 * BONI, 100),))
        self.assertEqual([lot.expires_at for lot in larder.lots], [100, 200])
        self.assertEqual(larder.lots[0].amount, 2 * BONI)

    def test_take_spans_several_lots_and_reports_each(self):
        """A transfer needs the expiry of every lot it moved, not just a total."""
        larder = Larder([Lot(2 * BONI, 100), Lot(5 * BONI, 200)])
        taken = larder.take(4 * BONI)
        self.assertEqual(taken, (Lot(2 * BONI, 100), Lot(2 * BONI, 200)))
        self.assertEqual(larder.total(), 3 * BONI)

    def test_take_everything_empties_the_larder(self):
        larder = Larder([Lot(2 * BONI, 100), Lot(3 * BONI, 200)])
        larder.take(5 * BONI)
        self.assertFalse(larder)

    def test_take_conserves_value(self):
        larder = Larder([Lot(7 * BONI, 100), Lot(11 * BONI, 200)])
        taken = larder.take(9 * BONI)
        self.assertEqual(sum(lot.amount for lot in taken) + larder.total(), 18 * BONI)

    def test_overdraft_raises(self):
        with self.assertRaises(ValueError):
            Larder([Lot(1 * BONI, 100)]).take(2 * BONI)

    # -- spoiling -------------------------------------------------------------

    def test_expire_drops_lots_at_or_past_their_expiry(self):
        larder = Larder([Lot(1 * BONI, 100), Lot(2 * BONI, 200)])
        self.assertEqual(larder.expire(100), 1 * BONI)
        self.assertEqual(larder.total(), 2 * BONI)

    def test_a_lot_is_spendable_right_up_to_its_expiry(self):
        larder = Larder([Lot(1 * BONI, 100)])
        self.assertEqual(larder.expire(99), 0)
        self.assertEqual(larder.total(), 1 * BONI)

    def test_expire_can_take_everything(self):
        larder = Larder([Lot(1 * BONI, 10), Lot(2 * BONI, 20)])
        self.assertEqual(larder.expire(50), 3 * BONI)
        self.assertFalse(larder)

    def test_blocks_left_never_goes_negative(self):
        self.assertEqual(Lot(1 * BONI, 100).blocks_left(40), 60)
        self.assertEqual(Lot(1 * BONI, 100).blocks_left(100), 0)
        self.assertEqual(Lot(1 * BONI, 100).blocks_left(500), 0)


class TestBalances(unittest.TestCase):
    def setUp(self):
        self.state = WorldState.genesis()

    def test_unknown_account_holds_nothing(self):
        self.assertEqual(self.state.balance_of(ALICE), 0)
        self.assertEqual(self.state.fertilizer_of(ALICE), 0)
        self.assertEqual(self.state.larder_of(ALICE).lots, ())

    def test_credit_then_debit(self):
        self.state.credit(ALICE, 10 * BONI, NEVER)
        self.state.debit(ALICE, 4 * BONI)
        self.assertEqual(self.state.balance_of(ALICE), 6 * BONI)

    def test_emptied_accounts_are_dropped(self):
        """The state hash must not depend on who once held a balance."""
        self.state.credit(ALICE, 5, NEVER)
        self.state.debit(ALICE, 5)
        self.assertNotIn(ALICE, self.state.larders)
        self.assertEqual(self.state.state_hash, WorldState.genesis().state_hash)

    def test_overdraft_raises(self):
        self.state.credit(ALICE, 1, NEVER)
        with self.assertRaises(ValueError):
            self.state.debit(ALICE, 2)

    def test_debiting_an_unknown_account_raises(self):
        with self.assertRaises(ValueError):
            self.state.debit(ALICE, 1)

    def test_negative_amounts_raise(self):
        with self.assertRaises(ValueError):
            self.state.credit(ALICE, -1, NEVER)

    def test_deposit_preserves_expiry_dates(self):
        """The mechanism that stops spoilage being laundered between keys."""
        self.state.credit(ALICE, 10 * BONI, 500)
        moved = self.state.debit(ALICE, 4 * BONI)
        self.state.deposit(BOB, moved)
        self.assertEqual(self.state.larder_of(BOB).lots, (Lot(4 * BONI, 500),))

    def test_supply_accounting_balances(self):
        self.state.mint(ALICE, 100 * BONI, NEVER)
        self.state.burn(ALICE, 30 * BONI)
        self.assertEqual(self.state.minted, 100 * BONI)
        self.assertEqual(self.state.burned, 30 * BONI)
        self.assertEqual(self.state.circulating_supply, self.state.minted - self.state.burned)


class TestSpoilage(unittest.TestCase):
    def setUp(self):
        self.state = WorldState.genesis()

    def test_spoiled_lots_become_fertilizer(self):
        self.state.mint(ALICE, 100 * BONI, expires_at=50)
        report = self.state.spoil(height=50)
        expected = 100 * BONI * self.state.economy.rot_fertilizer_bp // BP
        self.assertEqual(report, [(ALICE, 100 * BONI, expected)])
        self.assertEqual(self.state.balance_of(ALICE), 0)
        self.assertEqual(self.state.fertilizer_of(ALICE), expected)

    def test_spoilage_is_a_real_loss_not_a_conversion(self):
        """Less fertilizer comes out than $BONI went in, or nothing is at stake."""
        self.state.mint(ALICE, 100 * BONI, expires_at=50)
        self.state.spoil(height=50)
        self.assertLess(self.state.fertilizer_of(ALICE), 100 * BONI)
        self.assertEqual(self.state.rotted, 100 * BONI)

    def test_fresh_lots_survive(self):
        self.state.mint(ALICE, 100 * BONI, expires_at=50)
        self.assertEqual(self.state.spoil(height=49), [])
        self.assertEqual(self.state.balance_of(ALICE), 100 * BONI)

    def test_only_the_expired_lots_go(self):
        self.state.mint(ALICE, 10 * BONI, expires_at=50)
        self.state.mint(ALICE, 30 * BONI, expires_at=900)
        self.state.spoil(height=50)
        self.assertEqual(self.state.balance_of(ALICE), 30 * BONI)

    def test_an_account_that_rots_empty_is_still_an_account(self):
        """They hold fertilizer, and they are exactly who the UI must not lose."""
        self.state.mint(ALICE, 100 * BONI, expires_at=50)
        self.state.spoil(height=50)
        self.assertNotIn(ALICE, self.state.larders)
        self.assertIn(ALICE, self.state.accounts())

    def test_report_is_ordered_by_public_key(self):
        for key in (BOB, ALICE):
            self.state.mint(key, 10 * BONI, expires_at=50)
        report = self.state.spoil(height=50)
        self.assertEqual([key for key, _, _ in report], sorted([ALICE, BOB]))

    def test_supply_invariant_holds_across_spoilage(self):
        self.state.mint(ALICE, 100 * BONI, expires_at=50)
        self.state.burn(ALICE, 10 * BONI)
        self.state.spoil(height=50)
        self.assertEqual(
            self.state.circulating_supply,
            self.state.minted - self.state.burned - self.state.rotted,
        )

    def test_spending_fertilizer(self):
        self.state.mint(ALICE, 100 * BONI, expires_at=50)
        self.state.spoil(height=50)
        held = self.state.fertilizer_of(ALICE)
        self.state.spend_fertilizer(ALICE, held)
        self.assertEqual(self.state.fertilizer_of(ALICE), 0)
        self.assertNotIn(ALICE, self.state.fertilizer)

    def test_overspending_fertilizer_raises(self):
        with self.assertRaises(ValueError):
            self.state.spend_fertilizer(ALICE, 1)


class TestLand(unittest.TestCase):
    def setUp(self):
        self.state = WorldState.genesis(Economy(grid_width=4))

    def test_plots_are_numbered_sequentially_from_zero(self):
        self.assertEqual(self.state.mint_land(ALICE).land_id, 0)
        self.assertEqual(self.state.mint_land(BOB).land_id, 1)
        self.assertEqual(self.state.mint_land(ALICE).land_id, 2)

    def test_plots_start_fallow_with_derived_fertility(self):
        plot = self.state.mint_land(ALICE)
        self.assertFalse(plot.is_planted)
        self.assertEqual(plot.blight_bp, 0)
        self.assertGreaterEqual(plot.fertility_bp, self.state.economy.fertility_min_bp)

    def test_lands_of_returns_only_the_owners_plots_in_order(self):
        for owner in (ALICE, BOB, ALICE, ALICE):
            self.state.mint_land(owner)
        self.assertEqual([p.land_id for p in self.state.lands_of(ALICE)], [0, 2, 3])
        self.assertEqual(self.state.land_count_of(ALICE), 3)
        self.assertEqual(self.state.land_count_of(BOB), 1)

    def test_adjacency_counts_only_same_owner_neighbours(self):
        # Grid width 4: plots 0,1,2,3 fill row 0; 4,5,6,7 fill row 1.
        for owner in (ALICE, ALICE, BOB, BOB, ALICE):
            self.state.mint_land(owner)
        # Plot 0's neighbours are 1 (Alice) and 4 (Alice).
        self.assertEqual(self.state.adjacent_owned_count(self.state.farmlands[0]), 2)
        # Plot 2's neighbours are 1 (Alice), 3 (Bob) and 6 (does not exist).
        self.assertEqual(self.state.adjacent_owned_count(self.state.farmlands[2]), 1)

    def test_adjacency_ignores_plots_that_do_not_exist_yet(self):
        plot = self.state.mint_land(ALICE)
        self.assertEqual(self.state.adjacent_owned_count(plot), 0)


class TestCropLifecycle(unittest.TestCase):
    def setUp(self):
        self.plot = Farmland(land_id=1, owner=ALICE, fertility_bp=BP)

    def test_fallow_plot_is_never_ready(self):
        self.assertFalse(self.plot.is_planted)
        self.assertFalse(self.plot.is_ready(10_000))
        self.assertEqual(self.plot.growth_progress_bp(10_000), 0)

    def test_readiness_is_measured_in_block_height(self):
        self.plot.planted_at, self.plot.ready_at = 100, 200
        self.assertFalse(self.plot.is_ready(199))
        self.assertTrue(self.plot.is_ready(200))
        self.assertTrue(self.plot.is_ready(500))

    def test_growth_progress_runs_from_zero_to_full(self):
        self.plot.planted_at, self.plot.ready_at = 100, 200
        self.assertEqual(self.plot.growth_progress_bp(100), 0)
        self.assertEqual(self.plot.growth_progress_bp(150), BP // 2)
        self.assertEqual(self.plot.growth_progress_bp(200), BP)

    def test_growth_progress_is_clamped_outside_the_window(self):
        self.plot.planted_at, self.plot.ready_at = 100, 200
        self.assertEqual(self.plot.growth_progress_bp(50), 0)
        self.assertEqual(self.plot.growth_progress_bp(9_999), BP)

    def test_clearing_a_crop_also_clears_its_blight(self):
        self.plot.planted_at, self.plot.ready_at, self.plot.blight_bp = 10, 20, 5_000
        self.plot.clear_crop()
        self.assertFalse(self.plot.is_planted)
        self.assertEqual(self.plot.blight_bp, 0)


class TestStateHash(unittest.TestCase):
    """The hash is what lets two nodes detect divergence, so it must be exact."""

    def _populated(self):
        state = WorldState.genesis(Economy(grid_width=4))
        state.mint(ALICE, 500 * BONI, expires_at=1_440)
        state.mint(BOB, 120 * BONI, expires_at=1_500)
        state.fertilizer[ALICE] = 2 * BONI
        state.mint_land(ALICE)
        state.mint_land(BOB)
        state.claimed.update({ALICE, BOB})
        return state

    def test_is_32_bytes_and_stable(self):
        state = self._populated()
        self.assertEqual(len(state.state_hash), 32)
        self.assertEqual(state.state_hash, state.state_hash)

    def test_equal_states_hash_equally_regardless_of_insertion_order(self):
        forwards = WorldState.genesis()
        forwards.mint(ALICE, 5, NEVER)
        forwards.mint(BOB, 7, NEVER)

        backwards = WorldState.genesis()
        backwards.mint(BOB, 7, NEVER)
        backwards.mint(ALICE, 5, NEVER)

        self.assertEqual(forwards.state_hash, backwards.state_hash)

    def test_lot_expiry_is_committed_not_just_the_total(self):
        """Two accounts can hold the same balance and still differ.

        What spoils next block is part of the state, so a hash over totals alone
        would let two nodes agree while holding different futures.
        """
        early = WorldState.genesis()
        early.mint(ALICE, 10 * BONI, expires_at=100)

        late = WorldState.genesis()
        late.mint(ALICE, 10 * BONI, expires_at=900)

        self.assertEqual(early.balance_of(ALICE), late.balance_of(ALICE))
        self.assertNotEqual(early.state_hash, late.state_hash)

    def test_lot_splitting_is_committed(self):
        """One lot of 10 is not the same state as two lots of 5."""
        merged = WorldState.genesis()
        merged.mint(ALICE, 10 * BONI, expires_at=100)

        split = WorldState.genesis()
        split.mint(ALICE, 5 * BONI, expires_at=100)
        split.mint(ALICE, 5 * BONI, expires_at=200)

        self.assertEqual(merged.balance_of(ALICE), split.balance_of(ALICE))
        self.assertNotEqual(merged.state_hash, split.state_hash)

    def test_every_consensus_field_changes_the_hash(self):
        baseline = self._populated().state_hash
        mutations = {
            "balance": lambda s: s.credit(ALICE, 1, 1_440),
            "new lot": lambda s: s.credit(ALICE, 1, 9_999),
            "new account": lambda s: s.credit(b"\xcc" * 74, 1, 1_440),
            "fertilizer": lambda s: s.fertilizer.__setitem__(ALICE, 3 * BONI),
            "new fertilizer holder": lambda s: s.fertilizer.__setitem__(BOB, 1),
            "new plot": lambda s: s.mint_land(ALICE),
            "ownership": lambda s: setattr(s.farmlands[0], "owner", BOB),
            "fertility": lambda s: setattr(s.farmlands[0], "fertility_bp", 9_999),
            "planted_at": lambda s: setattr(s.farmlands[0], "planted_at", 3),
            "ready_at": lambda s: setattr(s.farmlands[0], "ready_at", 90),
            "blight": lambda s: setattr(s.farmlands[0], "blight_bp", 5_000),
            "land price": lambda s: setattr(s, "next_land_price", 999),
            "burned": lambda s: setattr(s, "burned", 42),
            "minted": lambda s: setattr(s, "minted", 42),
            "rotted": lambda s: setattr(s, "rotted", 42),
            "fertilizer_minted": lambda s: setattr(s, "fertilizer_minted", 42),
            "claimed": lambda s: s.claimed.add(b"\xdd" * 74),
        }
        for label, mutate in mutations.items():
            with self.subTest(field=label):
                state = self._populated()
                mutate(state)
                self.assertNotEqual(state.state_hash, baseline)

    def test_length_prefixing_prevents_field_boundary_collisions(self):
        """Concatenated keys must not be able to impersonate one another."""
        one = WorldState.genesis()
        one.credit(b"ab", 1, NEVER)
        one.credit(b"c", 1, NEVER)

        other = WorldState.genesis()
        other.credit(b"a", 1, NEVER)
        other.credit(b"bc", 1, NEVER)

        self.assertNotEqual(one.state_hash, other.state_hash)


class TestReporting(unittest.TestCase):
    def test_summary_reports_the_farm(self):
        state = WorldState.genesis(Economy(grid_width=4))
        state.mint(ALICE, 10 * BONI, NEVER)
        plot = state.mint_land(ALICE)
        plot.planted_at, plot.ready_at = 1, 101
        summary = state.summary()
        self.assertEqual(summary["accounts"], 1)
        self.assertEqual(summary["plots"], 1)
        self.assertEqual(summary["planted"], 1)
        self.assertEqual(summary["circulating_supply"], 10 * BONI)
        self.assertEqual(summary["rotted"], 0)

    def test_leaderboard_ranks_by_balance_then_plots(self):
        state = WorldState.genesis()
        state.mint(ALICE, 50 * BONI, NEVER)
        state.mint(BOB, 90 * BONI, NEVER)
        podium = state.leaderboard()
        self.assertEqual([row["public_key"] for row in podium], [BOB.hex(), ALICE.hex()])

    def test_leaderboard_includes_farmers_left_with_only_compost(self):
        state = WorldState.genesis()
        state.mint(ALICE, 50 * BONI, NEVER)
        state.mint(BOB, 10 * BONI, expires_at=50)
        state.spoil(height=50)
        podium = state.leaderboard()
        self.assertEqual([row["public_key"] for row in podium], [ALICE.hex(), BOB.hex()])
        self.assertEqual(podium[1]["balance"], 0)
        self.assertGreater(podium[1]["fertilizer"], 0)

    def test_leaderboard_is_total_so_every_node_renders_the_same_podium(self):
        state = WorldState.genesis()
        for key in (ALICE, BOB, b"\xc3" * 74):
            state.mint(key, 10 * BONI, NEVER)  # a three-way tie
        self.assertEqual(state.leaderboard(), state.leaderboard())

    def test_leaderboard_honours_its_limit(self):
        state = WorldState.genesis()
        for i in range(20):
            state.mint(bytes([i]) * 74, (i + 1) * BONI, NEVER)
        self.assertEqual(len(state.leaderboard(limit=5)), 5)


if __name__ == "__main__":
    unittest.main()
