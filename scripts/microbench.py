#!/usr/bin/env python3
"""Where a node's CPU actually goes.

``benchmark.py`` measures the network. This measures the primitives underneath
it, because the network numbers are hard to interpret without them: a chain that
slows down under load is only interesting once you know *which* operation it is
spending its time in.

The answer, on this project, is not the one the course's framing would suggest.
Proof-of-Work is designed to be the expensive part, and here it is nowhere near
it -- a signature check costs thousands of times more than a hash, and every node
does one per transaction, twice (once when it is gossiped, once when the block
carrying it is validated).

Run::

    python scripts/microbench.py
"""

from __future__ import annotations

import _bootstrap  # noqa: F401  (adds src/ to sys.path)

import argparse
import hashlib
import time

from ipv8.keyvault.crypto import default_eccrypto

from blockchain.consensus import check_block
from blockchain.core import Block, mine
from blockchain.crypto import Identity
from blockchain.execution import Claim, StateMachine, WorldState, signed


def timed(operation, repeats: int) -> float:
    """Milliseconds per call, best-effort: no warm-up games, just the mean."""
    start = time.perf_counter()
    for _ in range(repeats):
        operation()
    return (time.perf_counter() - start) / repeats * 1000


def bench_hashing(repeats: int) -> None:
    header = b"\x00" * 88  # the real header size: tag, heights, two hashes, nonce
    per_call = timed(lambda: hashlib.sha256(header).digest(), repeats)
    rate = 1000 / per_call
    print(
        f"  SHA-256 of a block header   {per_call * 1000:8.3f} us   {rate:>12,.0f} hashes/s"
    )
    for difficulty in (12, 16, 20):
        print(
            f"    difficulty {difficulty:>2}: {1 << difficulty:>10,} expected hashes"
            f"  ->  {(1 << difficulty) / rate:8.2f} s of one core"
        )


def bench_signatures(repeats: int) -> None:
    print("\n  Signatures, by IPv8 security level:")
    print(f"    {'level':>12}{'pubkey':>9}{'sig':>7}{'sign':>11}{'verify':>11}")
    for level in ("medium", "curve25519"):
        key = default_eccrypto.generate_key(level)
        public = key.pub().key_to_bin()
        data = b"a transaction's signing payload"
        signature = default_eccrypto.create_signature(key, data)
        verifier = default_eccrypto.key_from_public_bin(public)

        sign_ms = timed(lambda: default_eccrypto.create_signature(key, data), repeats)
        verify_ms = timed(
            lambda: default_eccrypto.is_valid_signature(verifier, data, signature),
            repeats,
        )
        print(
            f"    {level:>12}{len(public):>8}B{len(signature):>6}B"
            f"{sign_ms:>8.3f} ms{verify_ms:>8.3f} ms"
        )
    print(
        "\n    'medium' is sect409k1, a 409-bit binary-field curve, and it is what\n"
        "    this project runs on -- it is IPv8's default and the course's example\n"
        "    keys use it. curve25519 is offered by the same library, verifies an\n"
        "    order of magnitude faster and puts 94 fewer bytes on the wire per\n"
        "    transaction. Changing it is a one-line default and a new genesis."
    )


def bench_block_validation(repeats: int) -> None:
    print("\n  Validating a block, by how many transactions it carries:")
    identity = Identity.generate()
    genesis = Block.genesis()
    for count in (0, 1, 8, 32):
        txs = tuple(signed(identity, Claim(), nonce=n) for n in range(count))
        block = mine(Block.create(1, genesis.block_hash, txs, timestamp=1), 4)
        per_call = timed(
            lambda: check_block(block, difficulty=4, parent=genesis), repeats
        )
        print(f"    {count:>3} transactions  {per_call:>8.3f} ms")
    print(
        "\n    Linear in the transaction count, and the slope is the signature\n"
        "    check. This is the cost every node pays for every block, which is\n"
        "    why the propagation delay in benchmark.py grows with block size."
    )


def bench_execution(repeats: int) -> None:
    print("\n  Execution and state commitment:")
    identity = Identity.generate()
    machine = StateMachine(WorldState.genesis())
    block = Block.create(
        1, Block.genesis().block_hash, (signed(identity, Claim()),), timestamp=1
    )
    apply_ms = timed(
        lambda: StateMachine(WorldState.genesis()).apply_block(block), repeats
    )
    machine.apply_block(block)
    root_ms = timed(lambda: machine.state.state_root, repeats)
    print(f"    apply a 1-tx block         {apply_ms:>8.3f} ms")
    print(f"    recompute the state root   {root_ms:>8.3f} ms")
    print(
        "\n    The state root is recomputed from the whole world on every call --\n"
        "    there is no incremental commitment. Cheap while the world is small,\n"
        "    and the first thing that would need a Merkle tree over accounts if it\n"
        "    were not."
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repeats", type=int, default=300)
    args = p.parse_args()

    print("═══ primitive costs (single-threaded, this machine) ═══\n")
    bench_hashing(args.repeats * 300)
    bench_signatures(args.repeats)
    bench_block_validation(max(20, args.repeats // 10))
    bench_execution(max(20, args.repeats // 10))

    print(
        "\n═══ the point ═══\n"
        "  Proof-of-Work is the cheapest thing a node does per transaction, and\n"
        "  asymmetric cryptography is the most expensive. Mining at the rate used\n"
        "  in the demos costs a few milliseconds of CPU per second; verifying the\n"
        "  transactions that arrive in the same second costs hundreds. Any effort\n"
        "  spent making this chain faster belongs in the signature scheme, not in\n"
        "  the miner."
    )


if __name__ == "__main__":
    main()
