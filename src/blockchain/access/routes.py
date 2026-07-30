"""Request routing: HTTP verbs and paths in, status codes and dicts out.

Deliberately free of sockets, headers and streams. :func:`handle` is a pure
function of a :class:`~blockchain.access.node.FarmNode` plus the request, which
makes every endpoint testable by calling it directly, and leaves
:mod:`blockchain.access.server` with nothing to do but move bytes.

The contract these routes implement is written down in ``docs/api.md``; the React
client in ``web/`` is built against the same document.

Two conventions run through everything here:

- **Amounts are integers in base units** and rates are basis points. No floats
  cross this boundary, so a client cannot accidentally round money.
- **Unknown accounts are zeroed, not missing.** Asking about a key that has never
  transacted is a normal thing for a UI to do, so it answers with an empty
  account rather than a 404.
"""

from __future__ import annotations

from blockchain.access.node import FarmNode
from blockchain.execution import (
    BONI,
    BP,
    BuyLand,
    Claim,
    Fertilize,
    Harvest,
    Plant,
    Transfer,
)
from blockchain.execution.economy import coords, fertilizer_effect

#: Cap on any ``limit`` query parameter, so one request cannot ask for the world.
MAX_LIMIT = 200

#: An offer large enough that :func:`fertilizer_effect` is bounded by the plot's
#: headroom rather than by the amount, which is how the map reports the maximum
#: speed-up available on a crop.
_UNLIMITED_FERTILIZER = 1 << 62

_ACTION_BUILDERS: dict[str, tuple[str, ...]] = {
    "claim": (),
    "buy_land": (),
    "plant": ("land_id",),
    "harvest": ("land_id",),
    "transfer": ("to", "amount"),
    "fertilize": ("land_id", "amount"),
}


class ApiError(Exception):
    """A request that cannot be served, carrying the status to answer with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def handle(
    node: FarmNode,
    method: str,
    path: str,
    query: dict[str, list[str]],
    body: dict | None,
) -> tuple[int, dict | list]:
    """Route one request. Raises :class:`ApiError` for anything unserveable."""
    parts = [part for part in path.strip("/").split("/") if part]
    if not parts or parts[0] != "api":
        raise ApiError(404, "not found")
    route = parts[1:]

    if method == "GET":
        return _get(node, route, query)
    if method == "POST":
        return _post(node, route, body or {})
    raise ApiError(405, f"method not allowed: {method}")


# -- reads --------------------------------------------------------------------


def _get(
    node: FarmNode, route: list[str], query: dict[str, list[str]]
) -> tuple[int, dict | list]:
    if route == ["health"]:
        return 200, {"ok": True, "height": node.height}
    if route == ["chain"]:
        return 200, _chain(node)
    if route == ["map"]:
        return 200, _map(node)
    if route == ["wallets"]:
        # Creation order, not key order: a wallet switcher should list them the
        # way a person made them, and sorting by public key is arbitrary to a
        # human even though it is deterministic.
        return 200, [
            {"public_key": key.hex(), "label": wallet.label}
            for key, wallet in node.wallets.items()
        ]
    if route == ["leaderboard"]:
        return 200, _leaderboard(node, _limit(query, default=10))
    if route == ["blocks"]:
        return 200, [
            _block(mined) for mined in node.recent_blocks(_limit(query, default=10))
        ]
    if route == ["activity"]:
        return 200, node.recent_activity(_limit(query, default=30))
    if len(route) == 2 and route[0] == "accounts":
        return 200, _account(node, _public_key(route[1]))
    raise ApiError(404, "not found")


def _chain(node: FarmNode) -> dict:
    state = node.state
    economy = node.economy
    return {
        "height": node.height,
        "head_hash": node.head.block.block_id,
        "state_root": node.head.state_root,
        "difficulty": node.difficulty,
        "base_units": BONI,
        "bp": BP,
        "economy": {
            "grid_width": economy.grid_width,
            "blocks_per_day": economy.blocks_per_day,
            "rot_days": economy.rot_days,
            "rot_blocks": economy.rot_blocks,
            "growth_blocks": economy.growth_blocks,
            "gas_fee": economy.gas_fee,
            "seed_cost": economy.seed_cost,
            "starter_balance": economy.starter_balance,
            "relief_balance": economy.relief_balance,
            # Live state rather than rules, but the client reads them alongside
            # the numbers they are shown next to, so they travel together. See
            # docs/api.md.
            #
            # ``next_land_id`` exists because BUY_LAND takes no argument: plots
            # are minted sequentially, so which parcel a buyer gets is decided by
            # the chain, not chosen. Publishing it means a client can point at the
            # right square instead of guessing which one is next.
            "next_land_price": state.next_land_price,
            "next_land_id": len(state.farmlands),
            "adjacency_bonus_bp": economy.adjacency_bonus_bp,
            "blight_interval": economy.blight_interval,
            "blight_penalty_bp": economy.blight_penalty_bp,
            "rot_fertilizer_bp": economy.rot_fertilizer_bp,
            "growth_blocks_per_fertilizer": economy.growth_blocks_per_fertilizer,
            "fertilizer_min_growth_bp": economy.fertilizer_min_growth_bp,
            "base_yield_min": economy.base_yield_min,
            "base_yield_max": economy.base_yield_max,
        },
        "supply": {
            "circulating": state.circulating_supply,
            "minted": state.minted,
            "burned": state.burned,
            "rotted": state.rotted,
            "fertilizer_minted": state.fertilizer_minted,
        },
        "mempool": len(node.mempool),
    }


def _map(node: FarmNode) -> dict:
    state = node.state
    height = node.height
    plots = []
    for land_id in sorted(state.farmlands):
        plot = state.farmlands[land_id]
        x, y = coords(land_id, state.economy)
        # How much growing time is still buyable here, and what it would cost.
        # Computed by the chain rather than left to the client: the floor depends
        # on nominal growth, not remaining time, and a client reimplementing that
        # is a client that will eventually disagree with consensus.
        #
        # A quote is only offered while the crop can actually take it. The maths
        # alone would keep quoting headroom on a matured crop, because the floor
        # is measured from ``planted_at`` and stays below a passed ``ready_at``
        # forever, but the engine refuses to fertilize something already ready. A
        # quote the engine would reject is worse than no quote.
        purchasable = plot.is_planted and not plot.is_ready(height)
        headroom_blocks, headroom_cost = (
            fertilizer_effect(
                planted_at=plot.planted_at,
                ready_at=plot.ready_at,
                amount=_UNLIMITED_FERTILIZER,
                economy=state.economy,
            )
            if purchasable
            else (0, 0)
        )
        plots.append(
            {
                "land_id": land_id,
                "x": x,
                "y": y,
                "owner": plot.owner.hex(),
                "fertility_bp": plot.fertility_bp,
                "is_planted": plot.is_planted,
                "planted_at": plot.planted_at,
                "ready_at": plot.ready_at,
                "progress_bp": plot.growth_progress_bp(height),
                "is_ready": plot.is_ready(height),
                "blight_bp": plot.blight_bp,
                "adjacent_owned": state.adjacent_owned_count(plot),
                "fertilizer_headroom_blocks": headroom_blocks,
                "fertilizer_headroom_cost": headroom_cost,
            }
        )
    return {"grid_width": state.economy.grid_width, "plots": plots}


def _account(node: FarmNode, public_key: bytes) -> dict:
    state = node.state
    height = node.height
    rot_blocks = node.economy.rot_blocks
    return {
        "public_key": public_key.hex(),
        "label": node.label_of(public_key),
        "balance": state.balance_of(public_key),
        "fertilizer": state.fertilizer_of(public_key),
        "claimed": public_key in state.claimed,
        "plots": [plot.land_id for plot in state.lands_of(public_key)],
        "lots": [
            {
                "amount": lot.amount,
                "expires_at": lot.expires_at,
                "blocks_left": lot.blocks_left(height),
                # Share of shelf life still ahead of this batch, for a UI meter:
                # BP when just harvested, 0 the block it rots. Named for what it
                # measures rather than for days, which it never was.
                "freshness_bp": min(BP, lot.blocks_left(height) * BP // rot_blocks),
            }
            for lot in state.larder_of(public_key).lots
        ],
    }


def _leaderboard(node: FarmNode, limit: int) -> list[dict]:
    return [
        {**row, "label": node.label_of(bytes.fromhex(row["public_key"]))}
        for row in node.state.leaderboard(limit)
    ]


def _block(mined) -> dict:
    block = mined.block
    return {
        "index": block.index,
        "hash": block.block_id,
        "prev_hash": block.prev_hash.hex(),
        "timestamp": block.timestamp,
        "nonce": block.nonce,
        "tx_count": len(block.transactions),
        "merkle_root": block.merkle_root.hex(),
        "state_root": mined.state_root,
    }


# -- writes -------------------------------------------------------------------


def _post(node: FarmNode, route: list[str], body: dict) -> tuple[int, dict | list]:
    if route == ["wallets"]:
        wallet = node.create_wallet(_optional_str(body, "label"))
        return 201, {"public_key": wallet.public_key.hex(), "label": wallet.label}
    if route == ["actions"]:
        return _submit_action(node, body)
    if route == ["mine"]:
        return _mine(node)
    raise ApiError(404, "not found")


def _submit_action(node: FarmNode, body: dict) -> tuple[int, dict]:
    public_key = _public_key(_required(body, "public_key"))
    action = _build_action(body)
    try:
        transaction = node.submit(public_key, action)
    except KeyError:
        raise ApiError(400, "unknown wallet") from None
    except ValueError as error:
        raise ApiError(400, str(error)) from None

    # 202, not 200: the chain has agreed to *consider* this. Whether it succeeds
    # depends on state at execution time, which is a block away. The client reads
    # the verdict from /api/activity.
    return 202, {
        "accepted": True,
        "tx_id": transaction.tx_id,
        "mempool": len(node.mempool),
    }


def _build_action(body: dict):
    kind = _required(body, "type")
    if kind not in _ACTION_BUILDERS:
        raise ApiError(400, f"unknown action type: {kind}")
    for field in _ACTION_BUILDERS[kind]:
        _required(body, field)

    try:
        if kind == "claim":
            return Claim()
        if kind == "buy_land":
            return BuyLand()
        if kind == "plant":
            return Plant(land_id=_non_negative_int(body, "land_id"))
        if kind == "harvest":
            return Harvest(land_id=_non_negative_int(body, "land_id"))
        if kind == "fertilize":
            return Fertilize(
                land_id=_non_negative_int(body, "land_id"),
                amount=_non_negative_int(body, "amount"),
            )
        return Transfer(
            recipient=_public_key(_required(body, "to")),
            amount=_non_negative_int(body, "amount"),
        )
    except ValueError as error:
        # Raised by an action's own encode-time range checks.
        raise ApiError(400, str(error)) from None


def _mine(node: FarmNode) -> tuple[int, dict]:
    mined, receipts, events = node.mine_next()
    return 200, {**_block(mined), "receipts": receipts, "events": events}


# -- parsing ------------------------------------------------------------------


def _limit(query: dict[str, list[str]], default: int) -> int:
    values = query.get("limit")
    if not values:
        return default
    try:
        limit = int(values[0])
    except ValueError:
        raise ApiError(400, "limit must be an integer") from None
    if limit < 0:
        raise ApiError(400, "limit must not be negative")
    return min(limit, MAX_LIMIT)


def _required(body: dict, field: str):
    if field not in body or body[field] is None:
        raise ApiError(400, f"missing field: {field}")
    return body[field]


def _optional_str(body: dict, field: str) -> str | None:
    value = body.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ApiError(400, f"{field} must be a non-empty string")
    return value.strip()[:32]


def _non_negative_int(body: dict, field: str) -> int:
    value = body[field]
    # Reject bools explicitly: in Python they are ints, and `True` as a land id
    # would silently mean plot 1.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ApiError(400, f"{field} must be a non-negative integer")
    return value


def _public_key(value) -> bytes:
    if not isinstance(value, str):
        raise ApiError(400, "public key must be a hex string")
    try:
        key = bytes.fromhex(value)
    except ValueError:
        raise ApiError(400, "public key must be a hex string") from None
    if not key:
        raise ApiError(400, "public key must not be empty")
    return key
