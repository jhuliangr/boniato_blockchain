"""Tests for the state transition function.

Grouped around the guarantees the engine promises: the economic loop works, bad
input is rejected without mutating anything, the same blocks always produce the
same state, and nobody can spend or grow what they do not own.
"""

import unittest
from dataclasses import replace

from blockchain.core import Block, Transaction
from blockchain.crypto import Identity
from blockchain.execution.actions import (
    BuyLand,
    Claim,
    Fertilize,
    Harvest,
    Plant,
    Transfer,
    signed,
)
from blockchain.execution.economy import BONI, BP, Economy
from blockchain.execution.engine import BlockContext, StateMachine
from blockchain.execution.state import WorldState

#: A brisk ruleset so tests do not have to wait 100 blocks for a crop, nor 1440
#: for a boniato to rot.
FAST = Economy(
    grid_width=4,
    growth_blocks=10,
    blight_interval=5,
    blocks_per_day=4,
    rot_days=10,  # -> rot_blocks == 40
)

CONTEXT = BlockContext(height=1, entropy=b"\x77" * 32)

#: An expiry far beyond any test's horizon.
NEVER = 10**9


def context_at(height: int, entropy: bytes = b"\x77" * 32) -> BlockContext:
    return BlockContext(height=height, entropy=entropy)


class EngineTestCase(unittest.TestCase):
    """Shared fixture: two players on a fast economy."""

    def setUp(self):
        self.alice = Identity.generate()
        self.bob = Identity.generate()
        self.machine = StateMachine(WorldState.genesis(FAST), FAST)
        self.state = self.machine.state

    def run_action(self, identity, action, height=1, entropy=b"\x77" * 32):
        tx = signed(identity, action)
        return self.machine.apply_transaction(tx, context_at(height, entropy))

    def claim(self, identity):
        return self.run_action(identity, Claim())

    def fund(self, identity, amount, expires_at=NEVER):
        """Give an account balance directly, bypassing the game loop.

        Defaults to boniatos that outlive the test, so spoilage only interferes
        where a test asks it to.
        """
        self.state.mint(identity.public_key, amount, expires_at)


class TestClaim(EngineTestCase):
    def test_grants_the_starter_kit(self):
        receipt = self.claim(self.alice)
        self.assertTrue(receipt.ok)
        self.assertEqual(self.state.balance_of(self.alice.public_key), FAST.starter_balance)
        self.assertEqual(self.state.land_count_of(self.alice.public_key), FAST.starter_lands)

    def test_is_gas_exempt_so_a_new_key_can_join(self):
        """A fresh key holds nothing; charging gas would make the chain closed."""
        receipt = self.claim(self.alice)
        self.assertEqual(receipt.gas_burned, 0)
        self.assertEqual(self.state.burned, 0)

    def test_can_only_be_taken_once(self):
        self.claim(self.alice)
        receipt = self.claim(self.alice)
        self.assertFalse(receipt.ok)
        self.assertEqual(receipt.reason, "already claimed")
        self.assertEqual(self.state.balance_of(self.alice.public_key), FAST.starter_balance)

    def test_receiving_a_transfer_does_not_forfeit_the_kit(self):
        self.claim(self.alice)
        self.run_action(self.alice, Transfer(recipient=self.bob.public_key, amount=10 * BONI))
        self.assertTrue(self.claim(self.bob).ok)
        self.assertEqual(
            self.state.balance_of(self.bob.public_key), FAST.starter_balance + 10 * BONI
        )

    def test_starter_plots_do_not_move_the_land_price(self):
        self.claim(self.alice)
        self.claim(self.bob)
        self.assertEqual(self.state.next_land_price, FAST.genesis_land_price)


class TestTransfer(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.claim(self.alice)

    def test_moves_value_and_burns_gas(self):
        before = self.state.balance_of(self.alice.public_key)
        receipt = self.run_action(
            self.alice, Transfer(recipient=self.bob.public_key, amount=100 * BONI)
        )
        self.assertTrue(receipt.ok)
        self.assertEqual(self.state.balance_of(self.bob.public_key), 100 * BONI)
        self.assertEqual(
            self.state.balance_of(self.alice.public_key), before - 100 * BONI - FAST.gas_fee
        )
        self.assertEqual(self.state.burned, FAST.gas_fee)

    def test_boniatos_are_the_gas_so_supply_shrinks(self):
        """The portfolio talking point, asserted: transacting burns supply."""
        before = self.state.circulating_supply
        self.run_action(self.alice, Transfer(recipient=self.bob.public_key, amount=1 * BONI))
        self.assertEqual(self.state.circulating_supply, before - FAST.gas_fee)

    def test_cannot_send_more_than_the_balance_minus_gas(self):
        balance = self.state.balance_of(self.alice.public_key)
        receipt = self.run_action(self.alice, Transfer(self.bob.public_key, balance))
        self.assertFalse(receipt.ok)
        self.assertEqual(receipt.reason, "insufficient balance")
        self.assertEqual(self.state.balance_of(self.bob.public_key), 0)

    def test_rejects_zero_and_self_transfers(self):
        for action, reason in (
            (Transfer(self.bob.public_key, 0), "amount must be positive"),
            (Transfer(self.alice.public_key, 1 * BONI), "self-transfer"),
        ):
            with self.subTest(reason=reason):
                self.assertEqual(self.run_action(self.alice, action).reason, reason)

    def test_a_rejected_transfer_still_pays_gas(self):
        """Otherwise failing transactions would be free to spam."""
        before = self.state.balance_of(self.alice.public_key)
        self.run_action(self.alice, Transfer(self.bob.public_key, 10**9 * BONI))
        self.assertEqual(self.state.balance_of(self.alice.public_key), before - FAST.gas_fee)

    def test_an_account_that_cannot_pay_gas_is_refused_untouched(self):
        receipt = self.run_action(self.bob, Transfer(self.alice.public_key, 1))
        self.assertFalse(receipt.ok)
        self.assertEqual(receipt.reason, "cannot afford gas")
        self.assertEqual(self.state.burned, 0)


class TestBuyLand(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.claim(self.alice)

    def test_buys_the_next_plot_and_burns_the_price(self):
        price = self.state.next_land_price
        receipt = self.run_action(self.alice, BuyLand())
        self.assertTrue(receipt.ok)
        self.assertEqual(receipt.detail["land_id"], 1)  # plot 0 came with the kit
        self.assertEqual(self.state.land_count_of(self.alice.public_key), 2)
        self.assertEqual(self.state.burned, price + FAST.gas_fee)

    def test_the_price_rises_after_each_sale(self):
        first = self.state.next_land_price
        self.run_action(self.alice, BuyLand())
        self.assertGreater(self.state.next_land_price, first)

    def test_the_price_is_read_from_state_not_from_the_signed_action(self):
        """A buyer cannot lock in yesterday's price by signing early."""
        tx = signed(self.alice, BuyLand())
        self.state.next_land_price = 400 * BONI
        receipt = self.machine.apply_transaction(tx, CONTEXT)
        self.assertEqual(receipt.detail["price"], 400 * BONI)

    def test_cannot_buy_beyond_the_balance(self):
        self.state.next_land_price = 10**6 * BONI
        receipt = self.run_action(self.alice, BuyLand())
        self.assertFalse(receipt.ok)
        self.assertEqual(receipt.reason, "cannot afford land")
        self.assertEqual(self.state.land_count_of(self.alice.public_key), 1)


class TestPlantAndHarvest(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.claim(self.alice)
        self.plot_id = self.state.lands_of(self.alice.public_key)[0].land_id

    def test_planting_burns_a_seed_and_schedules_the_crop(self):
        receipt = self.run_action(self.alice, Plant(self.plot_id), height=1)
        self.assertTrue(receipt.ok)
        plot = self.state.farmlands[self.plot_id]
        self.assertTrue(plot.is_planted)
        self.assertEqual(plot.ready_at, 1 + FAST.growth_blocks)
        self.assertEqual(self.state.burned, FAST.gas_fee * 1 + FAST.seed_cost)

    def test_harvest_before_maturity_is_refused(self):
        self.run_action(self.alice, Plant(self.plot_id), height=1)
        receipt = self.run_action(self.alice, Harvest(self.plot_id), height=5)
        self.assertFalse(receipt.ok)
        self.assertIn("not ready", receipt.reason)
        self.assertTrue(self.state.farmlands[self.plot_id].is_planted)

    def test_harvest_mints_boniatos_and_clears_the_plot(self):
        self.run_action(self.alice, Plant(self.plot_id), height=1)
        before = self.state.balance_of(self.alice.public_key)
        receipt = self.run_action(self.alice, Harvest(self.plot_id), height=11)
        self.assertTrue(receipt.ok)
        self.assertGreater(receipt.minted, 0)
        self.assertEqual(
            self.state.balance_of(self.alice.public_key),
            before + receipt.minted - FAST.gas_fee,
        )
        self.assertFalse(self.state.farmlands[self.plot_id].is_planted)

    def test_the_loop_closes_replanting_is_possible(self):
        for height in (1, 11, 21):
            self.run_action(self.alice, Plant(self.plot_id), height=height)
            self.assertTrue(
                self.run_action(self.alice, Harvest(self.plot_id), height=height + 10).ok
            )

    def test_cannot_plant_twice_on_one_plot(self):
        self.run_action(self.alice, Plant(self.plot_id), height=1)
        receipt = self.run_action(self.alice, Plant(self.plot_id), height=2)
        self.assertEqual(receipt.reason, "already planted")
        self.assertEqual(self.state.farmlands[self.plot_id].planted_at, 1)

    def test_cannot_harvest_a_fallow_plot(self):
        self.assertEqual(
            self.run_action(self.alice, Harvest(self.plot_id)).reason, "nothing planted"
        )

    def test_cannot_farm_someone_elses_plot(self):
        self.fund(self.bob, 100 * BONI)
        for action in (Plant(self.plot_id), Harvest(self.plot_id)):
            with self.subTest(action=action):
                self.assertEqual(self.run_action(self.bob, action).reason, "not the owner")

    def test_cannot_farm_a_plot_that_does_not_exist(self):
        self.assertEqual(self.run_action(self.alice, Plant(9_999)).reason, "no such plot")
        self.assertEqual(self.run_action(self.alice, Harvest(9_999)).reason, "no such plot")

    def test_adjacent_plots_yield_more_than_scattered_ones(self):
        """The clustering incentive, isolated from every other modifier.

        A controlled experiment, because the modifiers it competes with are far
        bigger than it is: fertility alone spans 0.8x to 1.2x and the dice roll
        spans 5 to 15 $BONI, so comparing two *different* plots tells you
        nothing about a 20% bonus. Here both farms are the same size and the
        same plot is harvested by the same signed transaction, which pins the
        fertility, the roll and the crowding penalty. The only variable left is
        whether the farmer's other plot borders the one being harvested.
        """
        farmer = Identity.generate()
        plant = signed(farmer, Plant(land_id=0), nonce=100)
        harvest = signed(farmer, Harvest(land_id=0), nonce=101)

        def minted(second_plot: int) -> int:
            machine = StateMachine(WorldState.genesis(FAST), FAST)
            machine.state.mint(farmer.public_key, 1_000 * BONI, NEVER)
            stranger = Identity.generate().public_key
            # Fill the map with strangers, then hand our farmer just two plots.
            for land_id in range(second_plot + 1):
                plot = machine.state.mint_land(stranger)
                if land_id in (0, second_plot):
                    plot.owner = farmer.public_key
            machine.apply_transaction(plant, context_at(1))
            return machine.apply_transaction(harvest, context_at(11)).minted

        # On a grid four wide, plot 1 borders plot 0 and plot 6 does not.
        self.assertGreater(minted(second_plot=1), minted(second_plot=6))


class TestBlight(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.claim(self.alice)
        self.plot_id = self.state.lands_of(self.alice.public_key)[0].land_id

    def _block_at(self, height, txs=(), entropy=b"\x99" * 32):
        return Block.create(
            index=height, prev_hash=entropy, transactions=tuple(txs), timestamp=height
        )

    def test_strikes_a_planted_plot_on_schedule(self):
        self.run_action(self.alice, Plant(self.plot_id), height=1)
        _, events = self.machine.apply_block(self._block_at(FAST.blight_interval))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].land_id, self.plot_id)
        self.assertEqual(self.state.farmlands[self.plot_id].blight_bp, FAST.blight_penalty_bp)

    def test_spares_fallow_plots(self):
        _, events = self.machine.apply_block(self._block_at(FAST.blight_interval))
        self.assertEqual(events, [])

    def test_does_not_strike_off_schedule(self):
        self.run_action(self.alice, Plant(self.plot_id), height=1)
        _, events = self.machine.apply_block(self._block_at(FAST.blight_interval + 1))
        self.assertEqual(events, [])

    def test_reduces_the_harvest(self):
        self.run_action(self.alice, Plant(self.plot_id), height=1)
        harvest = signed(self.alice, Harvest(self.plot_id))

        struck = self.machine
        struck.state.farmlands[self.plot_id].blight_bp = FAST.blight_penalty_bp
        struck_amount = struck.apply_transaction(harvest, context_at(11)).minted

        healthy = StateMachine(WorldState.genesis(FAST), FAST)
        healthy.apply_transaction(signed(self.alice, Claim()), context_at(1))
        healthy.apply_transaction(signed(self.alice, Plant(self.plot_id)), context_at(1))
        healthy_amount = healthy.apply_transaction(harvest, context_at(11)).minted

        self.assertLess(struck_amount, healthy_amount)

    def test_repeated_strikes_cannot_destroy_more_than_the_crop(self):
        self.run_action(self.alice, Plant(self.plot_id), height=1)
        for multiple in range(1, 6):
            self.machine.apply_block(self._block_at(FAST.blight_interval * multiple))
        self.assertLessEqual(self.state.farmlands[self.plot_id].blight_bp, BP)

    def test_replanting_clears_the_blight(self):
        self.run_action(self.alice, Plant(self.plot_id), height=1)
        self.machine.apply_block(self._block_at(FAST.blight_interval))
        self.run_action(self.alice, Harvest(self.plot_id), height=11)
        self.run_action(self.alice, Plant(self.plot_id), height=12)
        self.assertEqual(self.state.farmlands[self.plot_id].blight_bp, 0)

    def test_pests_resolve_before_transactions_in_the_same_block(self):
        """A harvest cannot outrun the blight by sharing its block."""
        self.run_action(self.alice, Plant(self.plot_id), height=1)
        height = FAST.blight_interval * 4  # well past maturity
        harvest = signed(self.alice, Harvest(self.plot_id))
        receipts, events = self.machine.apply_block(self._block_at(height, [harvest]))
        self.assertEqual(len(events), 1)
        self.assertEqual(receipts[0].detail["blight_bp"], FAST.blight_penalty_bp)


class TestSpoilage(EngineTestCase):
    """Harvested boniatos last ten days and then become compost."""

    def _block_at(self, height, txs=()):
        return Block.create(
            index=height, prev_hash=b"\x99" * 32, transactions=tuple(txs), timestamp=height
        )

    def test_harvested_boniatos_carry_an_expiry(self):
        self.claim(self.alice)
        self.run_action(self.alice, Plant(0), height=1)
        receipt = self.run_action(self.alice, Harvest(0), height=11)
        self.assertEqual(receipt.detail["expires_at"], 11 + FAST.rot_blocks)

    def test_the_starter_kit_is_perishable_too(self):
        """There is no such thing as an imperishable boniato."""
        receipt = self.claim(self.alice)
        self.assertEqual(receipt.detail["expires_at"], 1 + FAST.rot_blocks)

    def test_a_batch_spoils_on_schedule_and_leaves_compost(self):
        self.claim(self.alice)
        held = self.state.balance_of(self.alice.public_key)
        _, events = self.machine.apply_block(self._block_at(1 + FAST.rot_blocks))
        self.assertEqual([event.name for event in events], ["rot"])
        self.assertEqual(events[0].rotted, held)
        self.assertEqual(self.state.balance_of(self.alice.public_key), 0)
        self.assertGreater(self.state.fertilizer_of(self.alice.public_key), 0)

    def test_a_batch_survives_right_up_to_its_expiry(self):
        self.claim(self.alice)
        _, events = self.machine.apply_block(self._block_at(FAST.rot_blocks))
        self.assertEqual(events, [])
        self.assertGreater(self.state.balance_of(self.alice.public_key), 0)

    def test_spoilage_settles_before_the_transactions_in_its_block(self):
        """A transaction cannot spend boniatos that expired at this very height."""
        self.claim(self.alice)
        self.run_action(self.alice, Plant(0), height=2)
        transfer = signed(self.alice, Transfer(self.bob.public_key, 10 * BONI))
        receipts, events = self.machine.apply_block(
            self._block_at(1 + FAST.rot_blocks, [transfer])
        )
        self.assertEqual([event.name for event in events], ["rot"])
        self.assertFalse(receipts[0].ok)
        self.assertEqual(receipts[0].reason, "cannot afford gas")

    def test_spending_takes_the_batch_closest_to_rotting(self):
        self.claim(self.alice)
        self.run_action(self.alice, Plant(0), height=1)
        self.run_action(self.alice, Harvest(0), height=11)  # a fresher batch
        lots = self.state.larder_of(self.alice.public_key).lots
        self.assertEqual(len(lots), 2)

        oldest_before = lots[0].amount
        self.run_action(self.alice, Transfer(self.bob.public_key, 5 * BONI), height=12)
        # Gas and the transfer both come out of the oldest batch, never the new one.
        self.assertEqual(
            self.state.larder_of(self.alice.public_key).lots[0].amount,
            oldest_before - 5 * BONI - FAST.gas_fee,
        )
        self.assertEqual(self.state.larder_of(self.alice.public_key).lots[1], lots[1])

    def test_a_transfer_hands_over_the_age_of_what_was_spent(self):
        """Otherwise spoilage is laundered by bouncing a batch between keys."""
        self.claim(self.alice)
        expiry = self.state.larder_of(self.alice.public_key).lots[0].expires_at
        self.run_action(self.alice, Transfer(self.bob.public_key, 10 * BONI), height=2)
        received = self.state.larder_of(self.bob.public_key).lots
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].expires_at, expiry)

    def test_bouncing_boniatos_between_keys_does_not_refresh_them(self):
        self.claim(self.alice)
        self.claim(self.bob)
        for height in range(2, 10):
            sender, recipient = (
                (self.alice, self.bob) if height % 2 == 0 else (self.bob, self.alice)
            )
            self.run_action(
                sender, Transfer(recipient.public_key, 100 * BONI), height=height
            )
        # Every batch still rots on the schedule set when it was first minted.
        for key in (self.alice.public_key, self.bob.public_key):
            for lot in self.state.larder_of(key).lots:
                self.assertLessEqual(lot.expires_at, 2 + FAST.rot_blocks)

    def test_supply_invariant_holds_across_spoilage(self):
        self.claim(self.alice)
        self.run_action(self.alice, Plant(0), height=1)
        self.machine.apply_block(self._block_at(1 + FAST.rot_blocks))
        self.assertEqual(
            self.state.circulating_supply,
            self.state.minted - self.state.burned - self.state.rotted,
        )


class TestRelief(EngineTestCase):
    """The escape hatch spoilage makes necessary."""

    def test_a_farmer_who_rots_out_can_get_planting_again(self):
        """Without this, an empty larder bricks an account permanently.

        Planting costs a seed and gas, both denominated in the boniatos the
        farmer no longer has, so there would be no path back.
        """
        self.claim(self.alice)
        self.machine.apply_block(
            Block.create(
                index=1 + FAST.rot_blocks,
                prev_hash=b"\x99" * 32,
                transactions=(),
                timestamp=1,
            )
        )
        self.assertEqual(self.state.balance_of(self.alice.public_key), 0)

        height = 2 + FAST.rot_blocks
        receipt = self.run_action(self.alice, Claim(), height=height)
        self.assertTrue(receipt.ok)
        self.assertEqual(receipt.detail["kind"], "relief")
        self.assertEqual(self.state.balance_of(self.alice.public_key), FAST.relief_balance)

        # And it funds a *whole* cycle, not just the planting: a grant that ran
        # out at the seed would leave them unable to pay for their own harvest.
        self.assertTrue(self.run_action(self.alice, Plant(0), height=height).ok)
        harvest = self.run_action(self.alice, Harvest(0), height=height + FAST.growth_blocks)
        self.assertTrue(harvest.ok, harvest.reason)
        self.assertGreater(self.state.balance_of(self.alice.public_key), 0)

    def test_relief_is_pinned_to_the_cost_of_one_cycle(self):
        """The minimum that works, so zeroing out to re-claim is never an income."""
        self.assertEqual(FAST.relief_balance, 2 * FAST.gas_fee + FAST.seed_cost)

    def test_relief_grants_no_land(self):
        self.claim(self.alice)
        self.state.debit(self.alice.public_key, self.state.balance_of(self.alice.public_key))
        self.run_action(self.alice, Claim(), height=2)
        self.assertEqual(self.state.land_count_of(self.alice.public_key), FAST.starter_lands)

    def test_a_solvent_farmer_cannot_claim_again(self):
        self.claim(self.alice)
        receipt = self.claim(self.alice)
        self.assertFalse(receipt.ok)
        self.assertEqual(receipt.reason, "already claimed")

    def test_relief_boniatos_perish_like_any_other(self):
        self.claim(self.alice)
        self.state.debit(self.alice.public_key, self.state.balance_of(self.alice.public_key))
        receipt = self.run_action(self.alice, Claim(), height=5)
        self.assertEqual(receipt.detail["expires_at"], 5 + FAST.rot_blocks)


class TestFertilize(EngineTestCase):
    """Compost buys back growing time."""

    def setUp(self):
        super().setUp()
        self.claim(self.alice)
        self.run_action(self.alice, Plant(0), height=1)
        self.compost(self.alice, 10 * BONI)

    def compost(self, identity, amount):
        """Put fertilizer in an account's heap directly."""
        self.state.fertilizer[identity.public_key] = amount

    #: Fertilizer buying 3 blocks of growth, comfortably inside FAST's headroom
    #: (growth_blocks=10 with a 50% floor leaves only 5 blocks to buy).
    THREE_BLOCKS = 3 * BONI // FAST.growth_blocks_per_fertilizer

    def test_fertilizing_brings_the_harvest_forward(self):
        ready_before = self.state.farmlands[0].ready_at
        receipt = self.run_action(self.alice, Fertilize(0, self.THREE_BLOCKS), height=2)
        self.assertTrue(receipt.ok)
        self.assertEqual(receipt.detail["blocks_cut"], 3)
        self.assertEqual(receipt.detail["refunded"], 0)
        self.assertEqual(self.state.farmlands[0].ready_at, ready_before - 3)

    def test_fertilizing_lets_a_crop_be_harvested_sooner(self):
        self.assertFalse(self.run_action(self.alice, Harvest(0), height=6).ok)
        self.run_action(self.alice, Fertilize(0, 1 * BONI), height=2)
        self.assertTrue(self.run_action(self.alice, Harvest(0), height=6).ok)

    def test_it_consumes_the_fertilizer_it_used(self):
        self.run_action(self.alice, Fertilize(0, self.THREE_BLOCKS), height=2)
        self.assertEqual(
            self.state.fertilizer_of(self.alice.public_key), 10 * BONI - self.THREE_BLOCKS
        )

    def test_a_crop_can_never_be_rushed_past_the_floor(self):
        """Otherwise a compost heap turns harvesting into an on-demand loop."""
        receipt = self.run_action(self.alice, Fertilize(0, 10 * BONI), height=2)
        self.assertTrue(receipt.ok)
        floor = 1 + FAST.growth_blocks * FAST.fertilizer_min_growth_bp // BP
        self.assertEqual(self.state.farmlands[0].ready_at, floor)

    def test_overshoot_is_refunded_rather_than_pocketed(self):
        receipt = self.run_action(self.alice, Fertilize(0, 10 * BONI), height=2)
        self.assertGreater(receipt.detail["refunded"], 0)
        self.assertEqual(
            receipt.detail["consumed"] + receipt.detail["refunded"], 10 * BONI
        )
        self.assertEqual(
            self.state.fertilizer_of(self.alice.public_key), receipt.detail["refunded"]
        )

    def test_dust_too_small_to_buy_a_block_is_rejected_not_eaten(self):
        before = self.state.fertilizer_of(self.alice.public_key)
        receipt = self.run_action(self.alice, Fertilize(0, 1), height=2)
        self.assertFalse(receipt.ok)
        self.assertEqual(receipt.reason, "would not shorten growth")
        self.assertEqual(self.state.fertilizer_of(self.alice.public_key), before)

    def test_repeated_fertilizing_converges_on_the_same_floor(self):
        """The floor is measured against nominal growth, not what remains."""
        for _ in range(5):
            self.run_action(self.alice, Fertilize(0, 1 * BONI), height=2)
        floor = 1 + FAST.growth_blocks * FAST.fertilizer_min_growth_bp // BP
        self.assertGreaterEqual(self.state.farmlands[0].ready_at, floor)

    def test_cannot_fertilize_without_compost(self):
        self.compost(self.alice, 0)
        self.assertEqual(
            self.run_action(self.alice, Fertilize(0, 1 * BONI), height=2).reason,
            "not enough fertilizer",
        )

    def test_cannot_fertilize_a_fallow_or_finished_crop(self):
        self.run_action(self.alice, Harvest(0), height=11)
        self.assertEqual(
            self.run_action(self.alice, Fertilize(0, 1 * BONI), height=12).reason,
            "nothing planted",
        )

    def test_cannot_fertilize_a_crop_that_is_already_ready(self):
        self.assertEqual(
            self.run_action(self.alice, Fertilize(0, 1 * BONI), height=11).reason,
            "already ready",
        )

    def test_cannot_fertilize_someone_elses_plot(self):
        self.fund(self.bob, 100 * BONI)
        self.compost(self.bob, 10 * BONI)
        self.assertEqual(
            self.run_action(self.bob, Fertilize(0, 1 * BONI), height=2).reason,
            "not the owner",
        )

    def test_cannot_fertilize_a_plot_that_does_not_exist(self):
        self.assertEqual(
            self.run_action(self.alice, Fertilize(9_999, 1 * BONI), height=2).reason,
            "no such plot",
        )

    def test_zero_is_rejected(self):
        self.assertEqual(
            self.run_action(self.alice, Fertilize(0, 0), height=2).reason,
            "amount must be positive",
        )

    def test_the_full_spoil_to_fertilize_loop(self):
        """End to end: let a harvest rot, then spend the compost on a crop."""
        machine = StateMachine(WorldState.genesis(FAST), FAST)
        farmer = Identity.generate()
        machine.apply_transaction(signed(farmer, Claim(), nonce=1), context_at(1))
        machine.apply_transaction(signed(farmer, Plant(0), nonce=2), context_at(1))
        machine.apply_transaction(signed(farmer, Harvest(0), nonce=3), context_at(11))

        # Wait for everything harvested to spoil.
        rot_height = 11 + FAST.rot_blocks
        _, events = machine.apply_block(
            Block.create(index=rot_height, prev_hash=b"\x01" * 32, transactions=(), timestamp=1)
        )
        self.assertTrue(any(event.name == "rot" for event in events))
        compost = machine.state.fertilizer_of(farmer.public_key)
        self.assertGreater(compost, 0)

        # Relief unsticks them, and the compost speeds the next crop up.
        machine.apply_transaction(signed(farmer, Claim(), nonce=4), context_at(rot_height))
        machine.apply_transaction(signed(farmer, Plant(0), nonce=5), context_at(rot_height))
        nominal_ready = machine.state.farmlands[0].ready_at
        receipt = machine.apply_transaction(
            signed(farmer, Fertilize(0, compost), nonce=6), context_at(rot_height)
        )
        self.assertTrue(receipt.ok, receipt.reason)
        self.assertLess(machine.state.farmlands[0].ready_at, nominal_ready)


class TestRejectionsLeaveNoTrace(EngineTestCase):
    def test_unsigned_and_forged_transactions_change_nothing(self):
        self.claim(self.alice)
        baseline = self.state.state_hash
        forged = replace(
            signed(self.alice, Transfer(self.bob.public_key, 10 * BONI)),
            signature=b"\x00" * 64,
        )
        receipt = self.machine.apply_transaction(forged, CONTEXT)
        self.assertFalse(receipt.ok)
        self.assertEqual(receipt.reason, "bad signature")
        self.assertEqual(self.state.state_hash, baseline)

    def test_undecodable_actions_are_rejected_without_charging(self):
        self.claim(self.alice)
        baseline = self.state.state_hash
        tx = Transaction.create(self.alice, action=b"\xfe\xed\xfa\xce")
        receipt = self.machine.apply_transaction(tx, CONTEXT)
        self.assertFalse(receipt.ok)
        self.assertEqual(receipt.reason, "undecodable action")
        self.assertEqual(self.state.state_hash, baseline)

    def test_phase_two_dummy_transactions_are_accepted_as_no_ops(self):
        """The gossip experiments must keep running against a farming chain."""
        baseline = self.state.state_hash
        receipt = self.machine.apply_transaction(Transaction.create(self.alice), CONTEXT)
        self.assertTrue(receipt.ok)
        self.assertEqual(receipt.action, "noop")
        self.assertEqual(self.state.state_hash, baseline)

    def test_a_block_full_of_rubbish_does_not_crash_the_node(self):
        rubbish = [
            Transaction.create(self.alice, action=bytes([tag, 0, 0, 0])) for tag in range(256)
        ]
        block = Block.create(index=1, prev_hash=b"\x00" * 32, transactions=rubbish, timestamp=1)
        receipts, _ = self.machine.apply_block(block)
        self.assertEqual(len(receipts), len(rubbish))


class TestDeterminism(EngineTestCase):
    """Two honest nodes replaying the same blocks must reach the same state."""

    def _chain(self):
        """Blocks covering the whole economic loop, including a blight."""
        plant_height, harvest_height = 1, FAST.blight_interval * 3
        return [
            Block.create(
                index=plant_height,
                prev_hash=b"\x01" * 32,
                transactions=(
                    signed(self.alice, Claim(), nonce=1),
                    signed(self.bob, Claim(), nonce=2),
                    signed(self.alice, Plant(0), nonce=3),
                    signed(self.bob, Plant(1), nonce=4),
                    signed(self.alice, BuyLand(), nonce=5),
                ),
                timestamp=1,
            ),
            Block.create(
                index=FAST.blight_interval,
                prev_hash=b"\x02" * 32,
                transactions=(
                    signed(self.alice, Transfer(self.bob.public_key, 5 * BONI), nonce=6),
                ),
                timestamp=2,
            ),
            Block.create(
                index=harvest_height,
                prev_hash=b"\x03" * 32,
                transactions=(
                    signed(self.alice, Harvest(0), nonce=7),
                    signed(self.bob, Harvest(1), nonce=8),
                ),
                timestamp=3,
            ),
        ]

    def _replay(self, chain):
        machine = StateMachine(WorldState.genesis(FAST), FAST)
        for block in chain:
            machine.apply_block(block)
        return machine.state

    def test_replaying_the_same_chain_reproduces_the_state_hash(self):
        chain = self._chain()
        self.assertEqual(self._replay(chain).state_hash, self._replay(chain).state_hash)

    def test_the_chain_actually_exercises_the_loop(self):
        """Guard the test above from passing on an empty, trivial state."""
        state = self._replay(self._chain())
        self.assertGreater(state.minted, 0)
        self.assertGreater(state.burned, 0)
        self.assertEqual(len(state.farmlands), 3)  # two starter plots plus one bought

    def test_entropy_comes_from_the_parent_hash_not_the_block_itself(self):
        """A miner must not be able to grind their own nonce for a fat harvest.

        Re-mining the block (a different nonce, hence a different block hash)
        must leave the harvest untouched; changing the *parent* must not.
        """
        self.claim(self.alice)
        self.run_action(self.alice, Plant(0), height=1)
        harvest = signed(self.alice, Harvest(0), nonce=42)

        def minted(prev_hash, nonce):
            machine = StateMachine(WorldState.genesis(FAST), FAST)
            machine.apply_transaction(signed(self.alice, Claim(), nonce=1), context_at(1))
            machine.apply_transaction(signed(self.alice, Plant(0), nonce=2), context_at(1))
            block = Block.create(
                index=11, prev_hash=prev_hash, transactions=(harvest,), timestamp=1
            ).with_nonce(nonce)
            return machine.apply_block(block)[0][0].minted

        parent = b"\x55" * 32
        self.assertEqual(minted(parent, nonce=1), minted(parent, nonce=99_999))
        self.assertNotEqual(minted(parent, nonce=1), minted(b"\x66" * 32, nonce=1))


class TestSupplyInvariant(EngineTestCase):
    def test_circulating_supply_always_equals_minted_minus_burned(self):
        """The one invariant that must survive every action, valid or not."""
        actions = [
            (self.alice, Claim()),
            (self.bob, Claim()),
            (self.alice, BuyLand()),
            (self.alice, Plant(0)),
            (self.bob, Plant(1)),
            (self.alice, Transfer(self.bob.public_key, 40 * BONI)),
            (self.alice, Transfer(self.bob.public_key, 10**9 * BONI)),  # rejected
            (self.alice, Harvest(0)),  # rejected, not ready
            (self.bob, Plant(0)),  # rejected, not the owner
            (self.alice, Claim()),  # rejected, already claimed
        ]
        for height, (identity, action) in enumerate(actions, start=1):
            self.run_action(identity, action, height=height)
            with self.subTest(action=action):
                self.assertEqual(
                    self.state.circulating_supply, self.state.minted - self.state.burned
                )

        self.run_action(self.alice, Harvest(0), height=20)
        self.assertEqual(self.state.circulating_supply, self.state.minted - self.state.burned)


if __name__ == "__main__":
    unittest.main()
