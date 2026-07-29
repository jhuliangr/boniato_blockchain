"""Tests for the economic rules.

The emphasis is on the two properties consensus depends on: the math is
deterministic, and it is integer-only (no float ever leaks into a result).
"""

import unittest

from blockchain.execution.economy import (
    BONI,
    BP,
    CROWDING_SCALE,
    DEFAULT_ECONOMY,
    Economy,
    blight_target,
    coords,
    crowding_scale,
    fertility_of,
    fertilizer_effect,
    fertilizer_from_rot,
    harvest_amount,
    is_blight_block,
    neighbours,
    next_land_price,
)

ENTROPY = b"\xab" * 32
TX_HASH = b"\xcd" * 32


class TestEconomyValidation(unittest.TestCase):
    def test_rejects_inconsistent_parameters(self):
        for kwargs in (
            {"grid_width": 0},
            {"base_yield_min": 10, "base_yield_max": 5},
            {"fertility_min_bp": 200, "fertility_max_bp": 100},
            {"land_price_growth_den": 0},
            {"blight_penalty_bp": BP + 1},
            {"blight_penalty_bp": -1},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Economy(**kwargs)


class TestGrid(unittest.TestCase):
    def setUp(self):
        self.economy = Economy(grid_width=4)

    def test_ids_fill_the_grid_row_by_row(self):
        self.assertEqual(coords(0, self.economy), (0, 0))
        self.assertEqual(coords(3, self.economy), (3, 0))
        self.assertEqual(coords(4, self.economy), (0, 1))
        self.assertEqual(coords(6, self.economy), (2, 1))

    def test_interior_plot_has_four_neighbours(self):
        self.assertEqual(neighbours(5, self.economy), (1, 4, 6, 9))

    def test_grid_does_not_wrap_across_rows(self):
        """Plot 3 and plot 4 differ by one id but sit on different rows."""
        self.assertNotIn(4, neighbours(3, self.economy))
        self.assertNotIn(3, neighbours(4, self.economy))

    def test_top_row_has_no_northern_neighbour(self):
        self.assertEqual(neighbours(0, self.economy), (1, 4))

    def test_adjacency_is_symmetric(self):
        for land_id in range(40):
            for neighbour in neighbours(land_id, self.economy):
                with self.subTest(land_id=land_id, neighbour=neighbour):
                    self.assertIn(land_id, neighbours(neighbour, self.economy))


class TestFertility(unittest.TestCase):
    def test_is_deterministic(self):
        self.assertEqual(fertility_of(42), fertility_of(42))

    def test_stays_within_the_configured_band(self):
        for land_id in range(300):
            value = fertility_of(land_id)
            self.assertGreaterEqual(value, DEFAULT_ECONOMY.fertility_min_bp)
            self.assertLessEqual(value, DEFAULT_ECONOMY.fertility_max_bp)

    def test_varies_between_plots(self):
        self.assertGreater(len({fertility_of(i) for i in range(50)}), 10)

    def test_is_an_integer(self):
        self.assertIsInstance(fertility_of(1), int)


class TestLandPriceCurve(unittest.TestCase):
    def test_price_strictly_increases(self):
        price = DEFAULT_ECONOMY.genesis_land_price
        for _ in range(200):
            raised = next_land_price(price)
            self.assertGreater(raised, price)
            price = raised

    def test_reaches_roughly_a_hundredfold_by_the_hundredth_plot(self):
        """The design target: plot 1 at 100 BONI, plot 100 near 10_000 BONI."""
        price = DEFAULT_ECONOMY.genesis_land_price
        for _ in range(99):
            price = next_land_price(price)
        self.assertGreater(price, 8_000 * BONI)
        self.assertLess(price, 12_000 * BONI)

    def test_curve_never_stalls_on_tiny_prices(self):
        """Flooring must not let a low price stop growing."""
        self.assertEqual(next_land_price(1), 2)

    def test_prices_are_integers(self):
        self.assertIsInstance(next_land_price(100 * BONI), int)


class TestCrowding(unittest.TestCase):
    def test_a_single_plot_is_unpenalised(self):
        self.assertEqual(crowding_scale(0), CROWDING_SCALE)
        self.assertEqual(crowding_scale(1), CROWDING_SCALE)

    def test_penalty_follows_one_over_root_n(self):
        self.assertEqual(crowding_scale(4), CROWDING_SCALE // 2)
        self.assertEqual(crowding_scale(9), CROWDING_SCALE // 3)
        self.assertEqual(crowding_scale(100), CROWDING_SCALE // 10)

    def test_is_monotonically_non_increasing(self):
        values = [crowding_scale(n) for n in range(1, 10_000)]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_total_income_still_rewards_expansion(self):
        """Diminishing returns must not make a bigger farm earn strictly less.

        This is the test that caught two generations of rounding bug. A naive
        ``BP // isqrt(n)`` inverts the incentive at four plots (``3 * 1.0x``
        beats ``4 * 0.5x``); refining it to ``BP**2 // isqrt(n * BP**2)`` only
        pushes the inversion out to 464 plots. Sweeping a wide range is what
        makes the difference visible, so keep the range wide.
        """
        totals = [n * crowding_scale(n) for n in range(1, 10_000)]
        self.assertEqual(totals, sorted(totals))


class TestHarvestAmount(unittest.TestCase):
    def _amount(self, **overrides):
        kwargs = {
            "entropy": ENTROPY,
            "land_id": 3,
            "tx_hash": TX_HASH,
            "fertility_bp": BP,
            "adjacent_owned": 0,
            "owner_land_count": 1,
            "blight_bp": 0,
        }
        kwargs.update(overrides)
        return harvest_amount(**kwargs)

    def test_is_deterministic(self):
        self.assertEqual(self._amount(), self._amount())

    def test_is_an_integer(self):
        self.assertIsInstance(self._amount(), int)

    def test_lies_within_the_base_band_at_neutral_modifiers(self):
        amount = self._amount()
        self.assertGreaterEqual(amount, DEFAULT_ECONOMY.base_yield_min)
        self.assertLessEqual(amount, DEFAULT_ECONOMY.base_yield_max)

    def test_varies_with_entropy(self):
        """Different parent blocks must roll different harvests."""
        amounts = {self._amount(entropy=bytes([i]) * 32) for i in range(40)}
        self.assertGreater(len(amounts), 10)

    def test_varies_between_plots_in_the_same_block(self):
        amounts = {self._amount(land_id=i) for i in range(40)}
        self.assertGreater(len(amounts), 10)

    def test_fertility_scales_the_yield(self):
        poor = self._amount(fertility_bp=5_000)
        rich = self._amount(fertility_bp=20_000)
        self.assertEqual(rich // 4, poor)

    def test_adjacency_bonus_increases_the_yield(self):
        alone = self._amount(adjacent_owned=0)
        clustered = self._amount(adjacent_owned=2)
        self.assertGreater(clustered, alone)

    def test_crowding_reduces_per_plot_yield(self):
        small_farm = self._amount(owner_land_count=1)
        big_farm = self._amount(owner_land_count=16)
        self.assertEqual(big_farm, small_farm // 4)

    def test_blight_destroys_its_share(self):
        healthy = self._amount(blight_bp=0)
        struck = self._amount(blight_bp=5_000)
        self.assertEqual(struck, healthy // 2)

    def test_total_blight_destroys_everything(self):
        self.assertEqual(self._amount(blight_bp=BP), 0)


class TestSpoilageParameters(unittest.TestCase):
    def test_shelf_life_converts_days_into_blocks(self):
        economy = Economy(blocks_per_day=144, rot_days=10)
        self.assertEqual(economy.rot_blocks, 1_440)

    def test_relief_covers_one_full_cycle(self):
        economy = Economy(gas_fee=2 * BONI, seed_cost=3 * BONI)
        self.assertEqual(economy.relief_balance, 7 * BONI)

    def test_rejects_nonsensical_spoilage_parameters(self):
        for kwargs in (
            {"blocks_per_day": 0},
            {"rot_days": 0},
            {"rot_fertilizer_bp": BP + 1},
            {"rot_fertilizer_bp": -1},
            {"growth_blocks_per_fertilizer": 0},
            {"fertilizer_min_growth_bp": BP + 1},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Economy(**kwargs)


class TestFertilizerFromRot(unittest.TestCase):
    def test_conversion_is_lossy(self):
        """Spoilage must destroy value, or hoarding costs nothing."""
        self.assertLess(fertilizer_from_rot(100 * BONI), 100 * BONI)

    def test_scales_with_the_loss(self):
        self.assertEqual(fertilizer_from_rot(200 * BONI), 2 * fertilizer_from_rot(100 * BONI))

    def test_is_an_integer(self):
        self.assertIsInstance(fertilizer_from_rot(7), int)

    def test_nothing_lost_yields_nothing(self):
        self.assertEqual(fertilizer_from_rot(0), 0)


class TestFertilizerEffect(unittest.TestCase):
    """Growth bought with compost, and the two kinds of waste it refunds."""

    def setUp(self):
        # Nominal growth of 100 blocks with a 50% floor: 50 blocks are buyable,
        # and each whole unit of fertilizer buys 10 of them.
        self.economy = Economy(growth_blocks=100, fertilizer_min_growth_bp=5_000)
        self.planted_at, self.ready_at = 0, 100

    def _effect(self, amount, ready_at=None):
        return fertilizer_effect(
            planted_at=self.planted_at,
            ready_at=self.ready_at if ready_at is None else ready_at,
            amount=amount,
            economy=self.economy,
        )

    def test_buys_ten_blocks_per_unit(self):
        self.assertEqual(self._effect(3 * BONI), (30, 3 * BONI))

    def test_stops_at_the_floor_and_refunds_the_overshoot(self):
        blocks_cut, consumed = self._effect(50 * BONI)
        self.assertEqual(blocks_cut, 50)  # never past 50% of nominal growth
        self.assertEqual(consumed, 5 * BONI)  # only the 5 units that fit

    def test_refunds_dust_that_cannot_buy_a_whole_block(self):
        blocks_cut, consumed = self._effect(BONI // 10 - 1)
        self.assertEqual((blocks_cut, consumed), (0, 0))

    def test_consumed_never_exceeds_what_was_offered(self):
        for amount in (1, 99, BONI, 3 * BONI + 7, 500 * BONI):
            with self.subTest(amount=amount):
                _, consumed = self._effect(amount)
                self.assertLessEqual(consumed, amount)

    def test_a_crop_already_at_the_floor_cannot_be_rushed_further(self):
        self.assertEqual(self._effect(50 * BONI, ready_at=50), (0, 0))

    def test_repeated_spending_converges_on_the_floor(self):
        """The floor is measured against nominal growth, not remaining time."""
        ready_at = self.ready_at
        for _ in range(20):
            blocks_cut, _ = self._effect(2 * BONI, ready_at=ready_at)
            ready_at -= blocks_cut
        self.assertEqual(ready_at, 50)

    def test_results_are_integers(self):
        blocks_cut, consumed = self._effect(3 * BONI + 1)
        self.assertIsInstance(blocks_cut, int)
        self.assertIsInstance(consumed, int)


class TestBlightSchedule(unittest.TestCase):
    def test_strikes_on_the_interval(self):
        self.assertTrue(is_blight_block(200))
        self.assertTrue(is_blight_block(400))

    def test_does_not_strike_between_intervals(self):
        self.assertFalse(is_blight_block(199))
        self.assertFalse(is_blight_block(201))

    def test_genesis_is_spared(self):
        self.assertFalse(is_blight_block(0))

    def test_can_be_disabled(self):
        peaceful = Economy(blight_interval=0)
        self.assertFalse(is_blight_block(200, peaceful))

    def test_target_is_deterministic_and_within_the_candidates(self):
        candidates = [1, 4, 9, 16]
        target = blight_target(ENTROPY, candidates)
        self.assertIn(target, candidates)
        self.assertEqual(target, blight_target(ENTROPY, candidates))

    def test_no_candidates_means_no_victim(self):
        self.assertIsNone(blight_target(ENTROPY, []))

    def test_target_varies_with_entropy(self):
        candidates = list(range(20))
        targets = {blight_target(bytes([i]) * 32, candidates) for i in range(40)}
        self.assertGreater(len(targets), 5)


if __name__ == "__main__":
    unittest.main()
