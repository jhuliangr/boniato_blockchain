"""A basic Proof-of-Work mechanism over :class:`Block`.

Week-1 goal ("a basic Proof-of-Work mechanism"): make producing a valid block
*computationally costly* but *cheap to verify*. We use the classic Hashcash /
Bitcoin scheme find a ``nonce`` such that the block's SHA-256 hash has at least
``difficulty`` leading **zero bits**.

Why leading *bits* (not bytes / hex chars): bit-granularity difficulty lets the
demo dial cost smoothly. Each extra required zero bit doubles the expected work,
so a small difficulty keeps the week-1 demo fast while still being real PoW.

Verification (:func:`has_proof_of_work`) is a single hash and a comparison
independent of how long mining took which is the whole point of PoW.
"""

from __future__ import annotations

from blockchain.core.block import Block

# Modest default: ~2^16 hashes expected per block. Fast enough for a live demo,
# slow enough that the work is visible. Callers override per scenario.
DEFAULT_DIFFICULTY = 16


def leading_zero_bits(digest: bytes) -> int:
    """Count the leading zero *bits* of ``digest`` (big-endian)."""
    bits = 0
    for byte in digest:
        if byte == 0:
            bits += 8
            continue
        # Zero bits above the most-significant set bit of this byte.
        bits += 8 - byte.bit_length()
        break
    return bits


def has_proof_of_work(block: Block, difficulty: int) -> bool:
    """``True`` iff ``block``'s hash clears the ``difficulty`` threshold.

    This is the cheap side of PoW: one hash, one comparison. A verifier never
    needs to know or trust how the miner found the nonce.
    """
    return leading_zero_bits(block.block_hash) >= difficulty


def mine(block: Block, difficulty: int = DEFAULT_DIFFICULTY, start_nonce: int = 0) -> Block:
    """Search for a nonce that gives ``block`` a valid Proof-of-Work.

    Returns a new :class:`Block` (same contents, mined ``nonce``) whose hash has
    at least ``difficulty`` leading zero bits. Runs until it finds one PoW is a
    search with no shortcut, so there is no bounded upper limit by design.
    """
    if difficulty < 0:
        raise ValueError("difficulty must be non-negative")
    nonce = start_nonce
    while True:
        candidate = block.with_nonce(nonce)
        if leading_zero_bits(candidate.block_hash) >= difficulty:
            return candidate
        nonce += 1
