#!/usr/bin/env python3
"""Run a single ledger peer as its own process.

Useful for a live demo or as the backend a DApp will later talk to. The node
has a *persistent* transaction identity (a ``.pem`` key), produces signed dummy
transactions, and prints its state periodically. Point it at an already-running
node with ``--connect`` to form a link.

Examples
--------
Start node A::

    python scripts/run_node.py --key ec1.pem --port 9001 --strategy push

Start node B and connect it to A::

    python scripts/run_node.py --key ec2.pem --port 9002 --connect 127.0.0.1:9001
"""

from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse
import asyncio

from ipv8.configuration import ConfigBuilder, Strategy, WalkerDefinition
from ipv8_service import IPv8

from blockchain.crypto import Identity
from blockchain.metrics import Metrics
from blockchain.network import BlockchainCommunity, NodeConfig, make_strategy


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--key",
        default="node.pem",
        help="path to the identity .pem (created if missing)",
    )
    p.add_argument("--port", type=int, default=0, help="UDP port (0 = auto)")
    p.add_argument("--strategy", choices=["push", "pull", "hybrid"], default="push")
    p.add_argument("--max-peers", type=int, default=10)
    p.add_argument("--tx-interval", type=float, default=5.0)
    p.add_argument("--tick-interval", type=float, default=2.0)
    p.add_argument(
        "--connect",
        action="append",
        default=[],
        metavar="HOST:PORT",
        help="peer to connect to (repeatable)",
    )
    p.add_argument("--status-interval", type=float, default=5.0)
    return p.parse_args()


async def main() -> None:
    args = parse_args()

    identity = Identity.from_file(args.key)
    node = NodeConfig(
        identity=identity,
        strategy=make_strategy(args.strategy),
        metrics=Metrics(),
        name="standalone",
        tx_interval=args.tx_interval,
        tick_interval=args.tick_interval,
    )

    builder = ConfigBuilder().clear_keys().clear_overlays()
    builder.set_log_level("ERROR")
    builder.add_ephemeral_key("my_peer")
    builder.set_address("0.0.0.0")
    builder.set_port(args.port)
    builder.add_overlay(
        "BlockchainCommunity",
        "my_peer",
        [WalkerDefinition(Strategy.RandomWalk, args.max_peers, {"timeout": 3.0})],
        [],
        {"max_peers": args.max_peers, "node": node},
        [],
    )
    instance = IPv8(
        builder.finalize(),
        extra_communities={"BlockchainCommunity": BlockchainCommunity},
    )
    await instance.start()
    community = instance.get_overlay(BlockchainCommunity)

    print(
        f"Node up — identity {identity.address[:16]}  address {community.my_estimated_lan}"
    )
    for target in args.connect:
        host, port = target.split(":")
        community.walk_to((host, int(port)))
        print(f"  seeding connection to {host}:{port}")

    try:
        while True:
            await asyncio.sleep(args.status_interval)
            s = community.node_summary()
            print(
                f"[{s['strategy']}] peers={s['peers']:2d}  mempool={s['mempool_size']:4d}  "
                f"root={s['merkle_root']}  sent={s['metrics']['packets_sent']}  "
                f"dup={s['metrics']['tx_duplicate']}"
            )
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await instance.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
