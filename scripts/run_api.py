#!/usr/bin/env python3
"""Serve one node's chain over HTTP, for the React client to play against.

Starts a single-node Boniato Chain and exposes it on ``http://127.0.0.1:8000``.
The wire contract is ``docs/api.md``; the client lives in ``web/``.

Two conveniences make this playable in a browser:

- **An auto-miner.** Height is the clock, so nothing grows, ages or rots unless
  blocks are produced. A background thread mines one every ``--block-time``
  seconds, empty or not. The client can also mine on demand via ``POST /api/mine``
  when a player does not want to wait.
- **Seed wallets.** ``--wallets`` pre-creates a few keys with friendly labels so
  the client has something to switch between on first load. Add more at any time
  through ``POST /api/wallets``.

Defaults are tuned for a live demo rather than realism: fast blocks, quick crops
and a short shelf life, so a boniato visibly rots within a few minutes instead of
ten real days. The production-shaped numbers are the defaults of
:class:`~blockchain.execution.economy.Economy`.

Keys and the chain live in memory only. Restarting starts a new world.

Examples
--------
Default demo settings::

    python scripts/run_api.py

Closer to the real economy, and slower::

    python scripts/run_api.py --growth-blocks 100 --blocks-per-day 144 --block-time 10

No auto-miner, so the client controls time entirely::

    python scripts/run_api.py --block-time 0
"""

from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse
import threading
import time

from blockchain.access import FarmNode, build_server
from blockchain.execution import Economy

DEFAULT_WALLETS = ("alice", "bob", "carol")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--host", default="127.0.0.1", help="interface to bind")
    p.add_argument("--port", type=int, default=8000, help="port to bind")
    p.add_argument(
        "--difficulty",
        type=int,
        default=10,
        help="leading zero bits per block. Low, so a block mines in well under "
        "the block time and the API never blocks on Proof-of-Work",
    )
    p.add_argument(
        "--block-time",
        type=float,
        default=3.0,
        help="seconds between auto-mined blocks (0 disables the auto-miner)",
    )
    p.add_argument(
        "--growth-blocks", type=int, default=20, help="blocks for a crop to mature"
    )
    p.add_argument(
        "--blocks-per-day",
        type=int,
        default=6,
        help="blocks that stand for one day, so the ten-day shelf life of a "
        "boniato is this many times ten blocks",
    )
    p.add_argument("--grid-width", type=int, default=8, help="plots per row on the map")
    p.add_argument(
        "--blight-interval",
        type=int,
        default=15,
        help="a pest strikes every N blocks (0 = never)",
    )
    p.add_argument(
        "--wallets",
        type=int,
        default=3,
        help="how many labelled wallets to pre-create for the client",
    )
    return p.parse_args()


def auto_mine(node: FarmNode, interval: float, stop: threading.Event) -> None:
    """Produce a block every ``interval`` seconds until asked to stop.

    Empty blocks are the point as much as full ones: without them the world
    freezes, since every deadline in the game is a block height.
    """
    while not stop.wait(interval):
        try:
            mined, receipts, events = node.mine_next()
        except Exception as error:  # pragma: no cover - keep the demo alive
            print(f"  miner  failed: {error}")
            continue
        if receipts or events:
            print(
                f"  block {mined.block.index}  {len(receipts)} tx, "
                f"{len(events)} event(s)  state={mined.state_root[:12]}…"
            )


def main() -> None:
    args = parse_args()
    economy = Economy(
        grid_width=args.grid_width,
        growth_blocks=args.growth_blocks,
        blocks_per_day=args.blocks_per_day,
        blight_interval=args.blight_interval,
    )
    node = FarmNode(economy=economy, difficulty=args.difficulty)

    for index in range(args.wallets):
        label = DEFAULT_WALLETS[index] if index < len(DEFAULT_WALLETS) else None
        wallet = node.create_wallet(label)
        print(f"  wallet  {wallet.label:<10} {wallet.public_key.hex()[:24]}…")

    print("\n🍠  BONIATO CHAIN  node + HTTP access layer")
    print(
        f"  economy      crops mature in {economy.growth_blocks} blocks; "
        f"boniatos keep for {economy.rot_days} days = {economy.rot_blocks} blocks"
    )
    print(f"  pests        every {economy.blight_interval or '-'} blocks")
    print(
        f"  mining       difficulty {args.difficulty}, "
        f"auto-mine every {args.block_time or '-'}s"
    )
    print(f"  serving      http://{args.host}:{args.port}/api/chain")
    print("\n  Custodial demo wallet: this node holds the private keys and signs")
    print("  on the client's behalf. Keys and chain are in memory only.\n")

    stop = threading.Event()
    if args.block_time > 0:
        miner = threading.Thread(
            target=auto_mine,
            args=(node, args.block_time, stop),
            daemon=True,
            name="auto-miner",
        )
        miner.start()

    server = build_server(node, args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopping")
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        # A parting summary, which doubles as a check that the world stayed sane.
        state = node.state
        print(f"  final height {node.height}, state root {state.state_root[:16]}…")
        print(
            f"  supply  minted {state.minted}  burned {state.burned}  rotted {state.rotted}"
        )
        time.sleep(0)


if __name__ == "__main__":
    main()
