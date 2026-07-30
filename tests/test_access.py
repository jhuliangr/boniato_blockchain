"""Tests for the HTTP access layer.

Routing is a pure function of the node, so these call it directly. No sockets, no
threads, no waiting: the same coverage a live server would give, minus the
flakiness. What is *not* covered here is the socket plumbing in
:mod:`blockchain.access.server`, which is deliberately thin enough to have no
behaviour of its own.

The contract under test is ``docs/api.md``. Where a test asserts a field name or
a status code, that document is the reason.
"""

import unittest

from blockchain.access import ApiError, FarmNode, handle
from blockchain.execution import BONI, BP, Claim, Economy, Plant

#: Fast enough that a test can grow a crop and rot a harvest in a few blocks.
FAST = Economy(grid_width=4, growth_blocks=3, blocks_per_day=1, blight_interval=0)


class AccessTestCase(unittest.TestCase):
    def setUp(self):
        self.node = FarmNode(economy=FAST, difficulty=1)
        self.alice = self.node.create_wallet("alice")
        self.bob = self.node.create_wallet("bob")

    # -- helpers --------------------------------------------------------------

    def get(self, path, **query):
        status, payload = handle(
            self.node, "GET", path, {k: [str(v)] for k, v in query.items()}, None
        )
        self.assertEqual(status, 200)
        return payload

    def post(self, path, body=None, expect=None):
        status, payload = handle(self.node, "POST", path, {}, body or {})
        if expect is not None:
            self.assertEqual(status, expect, payload)
        return status, payload

    def act(self, wallet, **body):
        return self.post("/api/actions", {"public_key": wallet.public_key.hex(), **body})

    def mine(self):
        _, payload = self.post("/api/mine", expect=200)
        return payload

    def claim_and_mine(self, wallet):
        self.act(wallet, type="claim")
        return self.mine()


class TestChainEndpoint(AccessTestCase):
    def test_reports_height_heads_and_scaling_factors(self):
        chain = self.get("/api/chain")
        self.assertEqual(chain["height"], 0)
        self.assertEqual(chain["base_units"], BONI)
        self.assertEqual(chain["bp"], BP)
        self.assertEqual(len(chain["head_hash"]), 64)
        self.assertEqual(len(chain["state_root"]), 64)

    def test_publishes_every_rule_the_client_needs(self):
        """The client must never hardcode an economic constant."""
        economy = self.get("/api/chain")["economy"]
        for field in (
            "grid_width",
            "blocks_per_day",
            "rot_days",
            "rot_blocks",
            "growth_blocks",
            "gas_fee",
            "seed_cost",
            "starter_balance",
            "relief_balance",
            "next_land_price",
            "adjacency_bonus_bp",
            "blight_interval",
            "blight_penalty_bp",
            "rot_fertilizer_bp",
            "growth_blocks_per_fertilizer",
            "fertilizer_min_growth_bp",
            "base_yield_min",
            "base_yield_max",
        ):
            self.assertIn(field, economy)
        self.assertEqual(economy["rot_blocks"], FAST.rot_blocks)

    def test_supply_starts_empty_because_there_is_no_pre_mine(self):
        supply = self.get("/api/chain")["supply"]
        self.assertEqual(
            supply,
            {"circulating": 0, "minted": 0, "burned": 0, "rotted": 0, "fertilizer_minted": 0},
        )

    def test_mempool_count_tracks_pending_work(self):
        self.act(self.alice, type="claim")
        self.assertEqual(self.get("/api/chain")["mempool"], 1)
        self.mine()
        self.assertEqual(self.get("/api/chain")["mempool"], 0)

    def test_land_price_is_live_state(self):
        self.claim_and_mine(self.alice)
        before = self.get("/api/chain")["economy"]["next_land_price"]
        self.act(self.alice, type="buy_land")
        self.mine()
        self.assertGreater(self.get("/api/chain")["economy"]["next_land_price"], before)


class TestMapEndpoint(AccessTestCase):
    def test_starts_empty(self):
        payload = self.get("/api/map")
        self.assertEqual(payload["grid_width"], FAST.grid_width)
        self.assertEqual(payload["plots"], [])

    def test_a_claimed_plot_appears_with_its_coordinates(self):
        self.claim_and_mine(self.alice)
        plots = self.get("/api/map")["plots"]
        self.assertEqual(len(plots), 1)
        self.assertEqual((plots[0]["land_id"], plots[0]["x"], plots[0]["y"]), (0, 0, 0))
        self.assertEqual(plots[0]["owner"], self.alice.public_key.hex())
        self.assertFalse(plots[0]["is_planted"])

    def test_growth_progress_advances_with_height(self):
        self.claim_and_mine(self.alice)
        self.act(self.alice, type="plant", land_id=0)
        self.mine()
        first = self.get("/api/map")["plots"][0]["progress_bp"]
        self.mine()
        self.assertGreater(self.get("/api/map")["plots"][0]["progress_bp"], first)

    def test_a_matured_crop_reports_itself_ready(self):
        self.claim_and_mine(self.alice)
        self.act(self.alice, type="plant", land_id=0)
        for _ in range(FAST.growth_blocks + 1):
            self.mine()
        plot = self.get("/api/map")["plots"][0]
        self.assertTrue(plot["is_ready"])
        self.assertEqual(plot["progress_bp"], BP)

    def test_plots_are_ordered_by_id(self):
        self.claim_and_mine(self.alice)
        self.claim_and_mine(self.bob)
        ids = [plot["land_id"] for plot in self.get("/api/map")["plots"]]
        self.assertEqual(ids, sorted(ids))


class TestAccountEndpoint(AccessTestCase):
    def test_an_unknown_key_is_zeroed_not_missing(self):
        """A UI asking about a stranger is normal, so it must not 404."""
        account = self.get("/api/accounts/" + "ab" * 20)
        self.assertEqual(account["balance"], 0)
        self.assertEqual(account["fertilizer"], 0)
        self.assertEqual(account["lots"], [])
        self.assertFalse(account["claimed"])

    def test_reports_the_starter_kit(self):
        self.claim_and_mine(self.alice)
        account = self.get("/api/accounts/" + self.alice.public_key.hex())
        self.assertEqual(account["label"], "alice")
        self.assertEqual(account["balance"], FAST.starter_balance)
        self.assertEqual(account["plots"], [0])
        self.assertTrue(account["claimed"])

    def test_lots_expose_the_spoilage_countdown(self):
        """The larder is the mechanic the UI most needs to surface."""
        self.claim_and_mine(self.alice)
        lots = self.get("/api/accounts/" + self.alice.public_key.hex())["lots"]
        self.assertEqual(len(lots), 1)
        self.assertEqual(lots[0]["amount"], FAST.starter_balance)
        self.assertEqual(lots[0]["expires_at"], 1 + FAST.rot_blocks)
        self.assertEqual(lots[0]["blocks_left"], FAST.rot_blocks)

    def test_freshness_runs_from_full_to_empty(self):
        """A ready-made meter: BP when harvested, 0 the block it rots."""
        self.claim_and_mine(self.alice)
        path = "/api/accounts/" + self.alice.public_key.hex()
        self.assertEqual(self.get(path)["lots"][0]["freshness_bp"], BP)
        for _ in range(FAST.rot_blocks - 1):
            self.mine()
        remaining = self.get(path)["lots"][0]
        self.assertEqual(remaining["blocks_left"], 1)
        self.assertLess(remaining["freshness_bp"], BP)
        self.assertGreater(remaining["freshness_bp"], 0)

    def test_the_countdown_shortens_as_blocks_pass(self):
        self.claim_and_mine(self.alice)
        path = "/api/accounts/" + self.alice.public_key.hex()
        first = self.get(path)["lots"][0]["blocks_left"]
        self.mine()
        self.assertEqual(self.get(path)["lots"][0]["blocks_left"], first - 1)

    def test_a_rotted_account_keeps_its_compost(self):
        self.claim_and_mine(self.alice)
        for _ in range(FAST.rot_blocks):
            self.mine()
        account = self.get("/api/accounts/" + self.alice.public_key.hex())
        self.assertEqual(account["balance"], 0)
        self.assertEqual(account["lots"], [])
        self.assertGreater(account["fertilizer"], 0)

    def test_a_malformed_key_is_a_client_error(self):
        for value in ("nothex", "abc"):  # not hex, and odd-length hex
            with self.subTest(value=value), self.assertRaises(ApiError) as caught:
                handle(self.node, "GET", f"/api/accounts/{value}", {}, None)
            self.assertEqual(caught.exception.status, 400)

    def test_omitting_the_key_entirely_is_a_missing_route(self):
        with self.assertRaises(ApiError) as caught:
            handle(self.node, "GET", "/api/accounts/", {}, None)
        self.assertEqual(caught.exception.status, 404)


class TestActionSubmission(AccessTestCase):
    def test_accepting_is_not_succeeding(self):
        """202 means queued. The verdict comes from the activity feed."""
        self.claim_and_mine(self.alice)  # so gas is affordable and not the reason
        status, payload = self.act(self.alice, type="harvest", land_id=99)
        self.assertEqual(status, 202)
        self.assertTrue(payload["accepted"])
        self.assertEqual(len(payload["tx_id"]), 64)

        self.mine()
        latest = self.get("/api/activity")[0]
        self.assertFalse(latest["ok"])
        self.assertEqual(latest["reason"], "no such plot")

    def test_every_action_type_is_routable(self):
        self.claim_and_mine(self.alice)
        submissions = [
            {"type": "buy_land"},
            {"type": "plant", "land_id": 0},
            {"type": "harvest", "land_id": 0},
            {"type": "fertilize", "land_id": 0, "amount": 1 * BONI},
            {"type": "transfer", "to": self.bob.public_key.hex(), "amount": 1 * BONI},
        ]
        for body in submissions:
            with self.subTest(body=body):
                status, _ = self.act(self.alice, **body)
                self.assertEqual(status, 202)

    def test_an_unknown_wallet_is_refused(self):
        with self.assertRaises(ApiError) as caught:
            handle(
                self.node,
                "POST",
                "/api/actions",
                {},
                {"public_key": "ab" * 20, "type": "claim"},
            )
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(caught.exception.message, "unknown wallet")

    def test_malformed_submissions_are_refused(self):
        bodies = [
            {},  # no wallet
            {"public_key": self.alice.public_key.hex()},  # no type
            {"public_key": self.alice.public_key.hex(), "type": "sabotage"},
            {"public_key": self.alice.public_key.hex(), "type": "plant"},  # no land_id
            {"public_key": self.alice.public_key.hex(), "type": "plant", "land_id": -1},
            {"public_key": self.alice.public_key.hex(), "type": "plant", "land_id": "0"},
            {"public_key": self.alice.public_key.hex(), "type": "transfer", "amount": 1},
            {"public_key": self.alice.public_key.hex(), "type": "harvest", "land_id": 2**40},
        ]
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(ApiError) as caught:
                handle(self.node, "POST", "/api/actions", {}, body)
            self.assertEqual(caught.exception.status, 400)

    def test_a_boolean_is_not_an_acceptable_land_id(self):
        """In Python ``True`` is an int, and would silently mean plot 1."""
        with self.assertRaises(ApiError):
            handle(
                self.node,
                "POST",
                "/api/actions",
                {},
                {"public_key": self.alice.public_key.hex(), "type": "plant", "land_id": True},
            )

    def test_nothing_is_queued_when_a_submission_is_refused(self):
        try:
            handle(self.node, "POST", "/api/actions", {}, {"type": "claim"})
        except ApiError:
            pass
        self.assertEqual(self.get("/api/chain")["mempool"], 0)


class TestMining(AccessTestCase):
    def test_mining_advances_height_and_returns_the_block(self):
        payload = self.mine()
        self.assertEqual(payload["index"], 1)
        self.assertEqual(payload["tx_count"], 0)
        self.assertEqual(len(payload["hash"]), 64)
        self.assertEqual(self.get("/api/chain")["height"], 1)

    def test_an_empty_block_is_not_a_no_op(self):
        """Height is the clock: empty blocks are what make crops grow."""
        self.claim_and_mine(self.alice)
        self.act(self.alice, type="plant", land_id=0)
        self.mine()
        before = self.get("/api/map")["plots"][0]["progress_bp"]
        self.mine()  # nothing pending
        self.assertGreater(self.get("/api/map")["plots"][0]["progress_bp"], before)

    def test_mining_drains_the_mempool_into_receipts(self):
        self.act(self.alice, type="claim")
        self.act(self.bob, type="claim")
        payload = self.mine()
        self.assertEqual(payload["tx_count"], 2)
        self.assertEqual(len(payload["receipts"]), 2)
        self.assertTrue(all(receipt["ok"] for receipt in payload["receipts"]))

    def test_system_events_are_reported_separately(self):
        self.claim_and_mine(self.alice)
        for _ in range(FAST.rot_blocks - 1):
            self.mine()
        payload = self.mine()
        self.assertEqual([event["action"] for event in payload["events"]], ["rot"])
        self.assertGreater(payload["events"][0]["detail"]["rotted"], 0)
        self.assertEqual(payload["events"][0]["detail"]["label"], "alice")

    def test_each_block_records_the_state_root_it_produced(self):
        first = self.claim_and_mine(self.alice)["state_root"]
        self.act(self.alice, type="buy_land")
        second = self.mine()["state_root"]
        self.assertNotEqual(first, second)
        self.assertEqual(self.get("/api/chain")["state_root"], second)

    def test_a_block_that_changes_nothing_leaves_the_state_root_alone(self):
        """Not a bug: height lives in the block, not in the state.

        Lot expiries are absolute heights, so time passing does not by itself
        alter a single state field. The root moves when the world moves, which is
        exactly the property that makes it useful for spotting divergence.
        """
        first = self.claim_and_mine(self.alice)["state_root"]
        self.assertEqual(self.mine()["state_root"], first)


class TestActivityFeed(AccessTestCase):
    def test_is_newest_first(self):
        self.claim_and_mine(self.alice)
        self.act(self.alice, type="plant", land_id=0)
        self.mine()
        feed = self.get("/api/activity")
        self.assertEqual(feed[0]["action"], "plant")
        self.assertGreaterEqual(feed[0]["height"], feed[-1]["height"])

    def test_records_rejections_with_their_reason(self):
        """Seeing *why* the chain said no is most of what makes it legible."""
        self.claim_and_mine(self.alice)
        self.act(self.alice, type="plant", land_id=7)
        self.mine()
        entry = self.get("/api/activity")[0]
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["reason"], "no such plot")
        self.assertEqual(entry["gas_burned"], FAST.gas_fee)  # the attempt still cost

    def test_includes_system_events(self):
        self.claim_and_mine(self.alice)
        for _ in range(FAST.rot_blocks):
            self.mine()
        self.assertIn("rot", [entry["action"] for entry in self.get("/api/activity", limit=50)])

    def test_honours_its_limit(self):
        for _ in range(5):
            self.act(self.alice, type="claim")
            self.mine()
        self.assertEqual(len(self.get("/api/activity", limit=2)), 2)


class TestAttribution(AccessTestCase):
    """A feed nobody can be identified in is a feed that cannot be filtered."""

    def test_receipts_name_their_signer(self):
        self.claim_and_mine(self.alice)
        entry = self.get("/api/activity")[0]
        self.assertEqual(entry["public_key"], self.alice.public_key.hex())
        self.assertEqual(entry["label"], "alice")

    def test_a_rejected_receipt_still_names_its_signer(self):
        self.claim_and_mine(self.alice)
        self.act(self.alice, type="plant", land_id=99)
        self.mine()
        entry = self.get("/api/activity")[0]
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["label"], "alice")

    def test_the_feed_can_be_filtered_to_one_wallet(self):
        self.claim_and_mine(self.alice)
        self.claim_and_mine(self.bob)
        feed = self.get("/api/activity", limit=50)
        mine = [e for e in feed if e["public_key"] == self.bob.public_key.hex()]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["action"], "claim")

    def test_a_blight_has_no_signer(self):
        pests = Economy(grid_width=4, growth_blocks=3, blocks_per_day=1, blight_interval=2)
        node = FarmNode(economy=pests, difficulty=1)
        wallet = node.create_wallet("dora")
        node.submit(wallet.public_key, Claim())
        node.mine_next()
        node.submit(wallet.public_key, Plant(land_id=0))
        node.mine_next()

        # System events resolve *before* a block's transactions, so the blight
        # block the crop was planted in cannot see it. Mine on until one strikes.
        events = []
        for _ in range(pests.blight_interval + 1):
            _, _, events = node.mine_next()
            if events:
                break
        self.assertEqual([entry["action"] for entry in events], ["blight"])
        self.assertEqual(events[0]["public_key"], "")
        self.assertEqual(events[0]["label"], "")

    def test_a_transfer_names_its_recipient_in_full(self):
        """A truncated IPv8 key names nobody: they all share an ASN.1 header."""
        self.claim_and_mine(self.alice)
        self.act(self.alice, type="transfer", to=self.bob.public_key.hex(), amount=5 * BONI)
        self.mine()
        entry = next(e for e in self.get("/api/activity") if e["action"] == "transfer")
        self.assertEqual(entry["detail"]["to"], self.bob.public_key.hex())

    def test_two_different_recipients_are_distinguishable(self):
        """The regression this guards: a prefix made every recipient identical."""
        carol = self.node.create_wallet("carol")
        self.claim_and_mine(self.alice)
        for recipient in (self.bob, carol):
            self.act(self.alice, type="transfer", to=recipient.public_key.hex(), amount=1 * BONI)
        self.mine()
        stubs = {
            e["detail"]["to"] for e in self.get("/api/activity") if e["action"] == "transfer"
        }
        self.assertEqual(len(stubs), 2)

    def test_an_unheld_key_is_fingerprinted_by_its_tail(self):
        stranger = bytes.fromhex("307e301006072a8648" + "ab" * 20)
        label = self.node.label_of(stranger)
        self.assertTrue(stranger.hex().endswith(label))
        # And two strangers sharing the standard prefix still read differently.
        other = bytes.fromhex("307e301006072a8648" + "cd" * 20)
        self.assertNotEqual(label, self.node.label_of(other))

    def test_a_rot_event_names_the_account_it_happened_to(self):
        self.claim_and_mine(self.alice)
        for _ in range(FAST.rot_blocks - 1):
            self.mine()
        _, _, events = self.node.mine_next()
        self.assertEqual(events[0]["action"], "rot")
        self.assertEqual(events[0]["public_key"], self.alice.public_key.hex())
        self.assertEqual(events[0]["label"], "alice")


class TestFertilizerHeadroom(AccessTestCase):
    """The chain does the fertilizer maths, so the client cannot drift from it."""

    def test_a_fallow_plot_offers_no_headroom(self):
        self.claim_and_mine(self.alice)
        plot = self.get("/api/map")["plots"][0]
        self.assertEqual(plot["fertilizer_headroom_blocks"], 0)
        self.assertEqual(plot["fertilizer_headroom_cost"], 0)

    def test_a_matured_crop_offers_no_headroom(self):
        """A quote the engine would reject is worse than no quote.

        The maths alone would keep quoting: the floor is measured from
        ``planted_at``, so it stays below a passed ``ready_at`` forever. But the
        engine refuses to fertilize a crop that is already ready.
        """
        self.claim_and_mine(self.alice)
        self.act(self.alice, type="plant", land_id=0)
        for _ in range(FAST.growth_blocks + 5):
            self.mine()
        plot = self.get("/api/map")["plots"][0]
        self.assertTrue(plot["is_ready"])
        self.assertEqual(plot["fertilizer_headroom_blocks"], 0)
        self.assertEqual(plot["fertilizer_headroom_cost"], 0)

    def test_a_quote_is_only_offered_when_the_engine_would_accept_it(self):
        """Ties the quote to the engine's own precondition, at every height."""
        self.claim_and_mine(self.alice)
        self.act(self.alice, type="plant", land_id=0)
        self.mine()
        for _ in range(FAST.growth_blocks + 3):
            plot = self.get("/api/map")["plots"][0]
            quoted = plot["fertilizer_headroom_blocks"] > 0
            engine_would_accept = plot["is_planted"] and not plot["is_ready"]
            with self.subTest(height=self.node.height):
                self.assertEqual(quoted, engine_would_accept)
            self.mine()

    def test_a_growing_crop_reports_what_could_be_bought(self):
        self.claim_and_mine(self.alice)
        self.act(self.alice, type="plant", land_id=0)
        self.mine()
        plot = self.get("/api/map")["plots"][0]
        floor = plot["planted_at"] + FAST.growth_blocks * FAST.fertilizer_min_growth_bp // BP
        self.assertEqual(plot["fertilizer_headroom_blocks"], plot["ready_at"] - floor)
        self.assertGreater(plot["fertilizer_headroom_cost"], 0)

    def test_the_quoted_cost_buys_exactly_the_quoted_blocks(self):
        """The quote must match what the engine actually does with it."""
        self.claim_and_mine(self.alice)
        for _ in range(FAST.rot_blocks):
            self.mine()  # rot the kit into compost
        self.act(self.alice, type="claim")
        self.mine()
        self.act(self.alice, type="plant", land_id=0)
        self.mine()

        quote = self.get("/api/map")["plots"][0]
        self.act(
            self.alice, type="fertilize", land_id=0, amount=quote["fertilizer_headroom_cost"]
        )
        self.mine()
        entry = next(e for e in self.get("/api/activity") if e["action"] == "fertilize")
        self.assertTrue(entry["ok"], entry["reason"])
        self.assertEqual(entry["detail"]["blocks_cut"], quote["fertilizer_headroom_blocks"])
        self.assertEqual(entry["detail"]["refunded"], 0)


class TestLandSupply(AccessTestCase):
    def test_the_chain_says_which_parcel_is_next(self):
        """``buy_land`` takes no argument, so the client must not have to guess."""
        self.assertEqual(self.get("/api/chain")["economy"]["next_land_id"], 0)
        self.claim_and_mine(self.alice)
        self.assertEqual(self.get("/api/chain")["economy"]["next_land_id"], 1)

    def test_buying_yields_the_parcel_the_chain_advertised(self):
        self.claim_and_mine(self.alice)
        promised = self.get("/api/chain")["economy"]["next_land_id"]
        self.act(self.alice, type="buy_land")
        payload = self.mine()
        self.assertEqual(payload["receipts"][0]["detail"]["land_id"], promised)


class TestWallets(AccessTestCase):
    def test_lists_the_seeded_wallets_in_creation_order(self):
        self.assertEqual([w["label"] for w in self.get("/api/wallets")], ["alice", "bob"])
        self.post("/api/wallets", {"label": "dora"}, expect=201)
        self.assertEqual(
            [w["label"] for w in self.get("/api/wallets")], ["alice", "bob", "dora"]
        )

    def test_creating_one_returns_its_key(self):
        status, payload = self.post("/api/wallets", {"label": "dora"}, expect=201)
        self.assertEqual(payload["label"], "dora")
        self.assertIn(payload["public_key"], [w["public_key"] for w in self.get("/api/wallets")])

    def test_a_label_is_optional(self):
        _, payload = self.post("/api/wallets", {}, expect=201)
        self.assertTrue(payload["label"])

    def test_a_blank_label_is_refused(self):
        with self.assertRaises(ApiError):
            handle(self.node, "POST", "/api/wallets", {}, {"label": "   "})


class TestLeaderboard(AccessTestCase):
    def test_ranks_farmers_and_labels_them(self):
        self.claim_and_mine(self.alice)
        self.claim_and_mine(self.bob)
        self.act(self.alice, type="transfer", to=self.bob.public_key.hex(), amount=100 * BONI)
        self.mine()
        podium = self.get("/api/leaderboard")
        self.assertEqual([row["label"] for row in podium], ["bob", "alice"])
        self.assertIn("fertilizer", podium[0])

    def test_honours_its_limit(self):
        self.claim_and_mine(self.alice)
        self.claim_and_mine(self.bob)
        self.assertEqual(len(self.get("/api/leaderboard", limit=1)), 1)


class TestRoutingErrors(AccessTestCase):
    def test_unknown_paths_are_404(self):
        for method, path in (
            ("GET", "/api/nope"),
            ("GET", "/"),
            ("GET", "/not-api/chain"),
            ("POST", "/api/nope"),
        ):
            with self.subTest(path=path), self.assertRaises(ApiError) as caught:
                handle(self.node, method, path, {}, {})
            self.assertEqual(caught.exception.status, 404)

    def test_unsupported_methods_are_405(self):
        with self.assertRaises(ApiError) as caught:
            handle(self.node, "DELETE", "/api/chain", {}, None)
        self.assertEqual(caught.exception.status, 405)

    def test_a_bad_limit_is_a_client_error(self):
        for value in ("banana", "-1"):
            with self.subTest(value=value), self.assertRaises(ApiError) as caught:
                handle(self.node, "GET", "/api/activity", {"limit": [value]}, None)
            self.assertEqual(caught.exception.status, 400)

    def test_an_enormous_limit_is_capped_not_refused(self):
        self.assertIsInstance(self.get("/api/activity", limit=10**9), list)

    def test_health_answers_before_anything_has_happened(self):
        self.assertEqual(self.get("/api/health"), {"ok": True, "height": 0})


class TestPlayableLoop(AccessTestCase):
    """The whole game, driven only through the API a browser would use."""

    def test_a_farmer_can_complete_the_loop_over_http(self):
        alice = self.alice.public_key.hex()

        # Onboard.
        self.act(self.alice, type="claim")
        self.mine()
        self.assertEqual(self.get(f"/api/accounts/{alice}")["plots"], [0])

        # Plant, and wait for the crop.
        self.act(self.alice, type="plant", land_id=0)
        self.mine()
        for _ in range(FAST.growth_blocks):
            self.mine()
        self.assertTrue(self.get("/api/map")["plots"][0]["is_ready"])

        # Harvest, and check the new batch has an expiry.
        before = self.get(f"/api/accounts/{alice}")["balance"]
        self.act(self.alice, type="harvest", land_id=0)
        self.mine()
        account = self.get(f"/api/accounts/{alice}")
        self.assertGreater(account["balance"], before)
        self.assertTrue(all(lot["blocks_left"] > 0 for lot in account["lots"]))

        # Buy more land.
        self.act(self.alice, type="buy_land")
        self.mine()
        self.assertEqual(self.get(f"/api/accounts/{alice}")["plots"], [0, 1])

        # And the books still balance.
        supply = self.get("/api/chain")["supply"]
        self.assertEqual(
            supply["circulating"], supply["minted"] - supply["burned"] - supply["rotted"]
        )

    def test_the_spoil_then_fertilize_loop_over_http(self):
        alice = self.alice.public_key.hex()
        self.act(self.alice, type="claim")
        self.mine()

        # Let the starter kit rot into compost.
        for _ in range(FAST.rot_blocks):
            self.mine()
        compost = self.get(f"/api/accounts/{alice}")["fertilizer"]
        self.assertGreater(compost, 0)

        # Relief, then plant, then spend the compost to bring the harvest forward.
        self.act(self.alice, type="claim")
        self.mine()
        self.act(self.alice, type="plant", land_id=0)
        self.mine()
        nominal = self.get("/api/map")["plots"][0]["ready_at"]

        self.act(self.alice, type="fertilize", land_id=0, amount=compost)
        self.mine()
        self.assertLess(self.get("/api/map")["plots"][0]["ready_at"], nominal)

        entry = next(e for e in self.get("/api/activity") if e["action"] == "fertilize")
        self.assertTrue(entry["ok"], entry["reason"])
        self.assertGreater(entry["detail"]["blocks_cut"], 0)


if __name__ == "__main__":
    unittest.main()
