#!/usr/bin/env python3
"""Week-1 demo: transactions -> Merkle-grouped block -> Proof-of-Work.

A single-process demonstration of the three components the week-1 progress
report must show working:

1. **Transactions** signed dummy transactions (nonce + public key + signature).
2. **Blocks** a batch of transactions grouped under one Merkle root.
3. **Proof-of-Work** mine each block until its hash clears a difficulty target.

It builds a small chain, then runs a "rainy day" check that tampering with a
block is detected. No network is needed everything here is the pure domain
layer, so the demo is deterministic and fast.

Examples
--------
Default run (3 blocks, 3 txs each, difficulty 18)::

    python scripts/mine_blocks.py

Heavier proof-of-work, more transactions::

    python scripts/mine_blocks.py --difficulty 22 --tx-per-block 8 --blocks 4
"""

from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse
import time

from blockchain.core import (
    Block,
    Transaction,
    has_proof_of_work,
    leading_zero_bits,
    mine,
)
from blockchain.crypto import Identity


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--blocks", type=int, default=3, help="how many blocks to mine")
    p.add_argument(
        "--tx-per-block", type=int, default=3, help="transactions grouped per block"
    )
    p.add_argument(
        "--difficulty",
        type=int,
        default=18,
        help="required leading zero bits of each block hash (higher = slower)",
    )
    return p.parse_args()


def make_transactions(identity: Identity, count: int, start_nonce: int) -> tuple[Transaction, ...]:
    """Create ``count`` signed transactions and confirm each verifies."""
    txs = tuple(
        Transaction.create(identity, nonce=start_nonce + i) for i in range(count)
    )
    assert all(tx.is_valid() for tx in txs), "a freshly signed tx failed to verify"
    return txs


def mine_chain(identity: Identity, n_blocks: int, tx_per_block: int, difficulty: int) -> list[Block]:
    """Build and mine a chain of ``n_blocks`` linked, proof-of-worked blocks."""
    chain: list[Block] = [Block.genesis(timestamp=0)]
    print(f"genesis  hash={chain[0].block_id[:16]}…\n")

    for height in range(1, n_blocks + 1):
        txs = make_transactions(identity, tx_per_block, start_nonce=height * 1000)
        prev = chain[-1]
        # `timestamp` is monotonic and passed in (the domain layer is clock-free
        # so it stays deterministic and testable).
        candidate = Block.create(
            index=height,
            prev_hash=prev.block_hash,
            transactions=txs,
            timestamp=int(time.time()),
        )

        started = time.perf_counter()
        block = mine(candidate, difficulty)
        elapsed = time.perf_counter() - started

        assert has_proof_of_work(block, difficulty)
        assert block.is_valid()
        print(
            f"block {height}  txs={len(block.transactions)}  "
            f"merkle={block.merkle_root.hex()[:12]}…  "
            f"nonce={block.nonce:<8} "
            f"zeros={leading_zero_bits(block.block_hash)}  "
            f"({elapsed:5.2f}s)  hash={block.block_id[:16]}…"
        )
        chain.append(block)
    return chain


def verify_chain(chain: list[Block], difficulty: int) -> bool:
    """Re-validate every link: PoW, self-consistency and prev_hash wiring."""
    for i in range(1, len(chain)):
        block, prev = chain[i], chain[i - 1]
        if block.prev_hash != prev.block_hash:
            return False
        if not block.is_valid():
            return False
        if not has_proof_of_work(block, difficulty):
            return False
    return True


def main() -> None:
    args = parse_args()
    identity = Identity.generate()
    print(f"miner identity: {identity.address[:16]}…")
    print(
        f"mining {args.blocks} block(s), {args.tx_per_block} tx/block, "
        f"difficulty={args.difficulty} leading zero bits\n"
    )

    chain = mine_chain(identity, args.blocks, args.tx_per_block, args.difficulty)

    # -- Sunny day: the honest chain verifies end to end. --------------------
    print()
    ok = verify_chain(chain, args.difficulty)
    print(f"sunny day  full-chain verification: {'PASS' if ok else 'FAIL'}")

    # -- Rainy day: tamper with a mined block; verification must reject it. ---
    tampered = chain[:]
    victim = tampered[1]
    forged_txs = victim.transactions[:-1] if victim.transactions else ()
    # Same header (nonce/merkle_root untouched) but a different tx set: the
    # Merkle root no longer commits to the transactions carried.
    from dataclasses import replace

    tampered[1] = replace(victim, transactions=forged_txs)
    rejected = not verify_chain(tampered, args.difficulty)
    print(f"rainy day  tampered block detected & rejected: {'PASS' if rejected else 'FAIL'}")


if __name__ == "__main__":
    main()
