"""Proof-of-Work searching that shares a thread with everything else.

:func:`blockchain.core.pow.mine` runs until it finds a nonce. That is the right
shape for a script and the wrong one for a peer: a node that disappears into a
hash loop stops answering its neighbours, and a hundred of them in one event
loop would take turns freezing the network.

:class:`Miner` slices the same search into bounded rounds. Each :meth:`step`
tries a fixed number of nonces and returns, so mining interleaves with gossip
instead of blocking it. Two useful properties fall out of that:

- **The mining rate is a knob.** Hashes per round times rounds per second is a
  node's hash rate, so a fleet can be given deliberately unequal power, and the
  fork rate can be tuned by making blocks easy or hard relative to how fast they
  propagate. Both matter for benchmarking: throughput is bounded by ``lambda *
  delta``, the mining rate times the propagation delay.
- **No threads.** The GIL would make a mining thread pointless anyway --
  ``hashlib`` only releases it for buffers far larger than an 88-byte block
  header, so parallel miners in one process would serialise regardless.

The template is rebuilt when the head moves or a new transaction shows up, and
**the nonce counter is never reset**. That detail is load-bearing. Trials are
independent -- one hash of one header either clears the target or does not -- so
there is no progress to lose in a rebuild, and carrying the counter forward
makes every trial a fresh one. Resetting it to zero instead would be quietly
fatal: with transactions arriving faster than a block is found, the miner would
rebuild every time and spend eternity re-testing the same low nonces without
ever completing a search. Real miners avoid the same trap by rolling an
extranonce rather than restarting.
"""

from __future__ import annotations

import time

from blockchain.consensus.ledger import Ledger
from blockchain.consensus.validation import MAX_BLOCK_TRANSACTIONS
from blockchain.core import Block
from blockchain.core.pow import leading_zero_bits

#: Nonces per :meth:`Miner.step`. About a millisecond of CPU, so a node stays
#: responsive between rounds whatever the difficulty is set to.
DEFAULT_HASHES_PER_ROUND = 2_000


class Miner:
    """A bounded, resumable Proof-of-Work search over a ledger's next block."""

    def __init__(
        self,
        ledger: Ledger,
        hashes_per_round: int = DEFAULT_HASHES_PER_ROUND,
        clock=time.time,
    ) -> None:
        self.ledger = ledger
        self.hashes_per_round = hashes_per_round
        self._clock = clock
        self._candidate: Block | None = None
        self._nonce = 0
        self.hashes_tried = 0
        self.blocks_found = 0

    # -- state ----------------------------------------------------------------

    @property
    def candidate(self) -> Block | None:
        """The template currently being searched, if any."""
        return self._candidate

    def reset(self) -> None:
        """Drop the current template; the next step builds a fresh one.

        The nonce counter deliberately survives -- see the module docstring.
        """
        self._candidate = None

    # -- searching ------------------------------------------------------------

    def step(self) -> Block | None:
        """Try a round of nonces. Returns a mined block, or ``None`` to continue.

        The returned block is *not* connected: the caller decides what to do
        with it, which is what lets a node connect it and announce it in one
        place rather than two.
        """
        candidate = self._template()
        difficulty = self.ledger.difficulty

        for _ in range(self.hashes_per_round):
            attempt = candidate.with_nonce(self._nonce)
            self._nonce += 1
            self.hashes_tried += 1
            if leading_zero_bits(attempt.block_hash) >= difficulty:
                self.blocks_found += 1
                self.reset()
                return attempt
        return None

    def _template(self) -> Block:
        """The block to search, rebuilt only when what it commits to changed."""
        pending = self.ledger.pending
        if self._candidate is not None and self._is_current(self._candidate, pending):
            return self._candidate

        self._candidate = self.ledger.candidate(timestamp=int(self._clock()))
        return self._candidate

    def _is_current(self, candidate: Block, pending: tuple) -> bool:
        """Does ``candidate`` still describe the block we ought to be mining?"""
        if candidate.prev_hash != self.ledger.chain.head_hash:
            return False  # somebody else won this height
        # Rebuild to pick up work that arrived while we were searching. A real
        # miner swaps templates without losing the round in progress; here the
        # round is a millisecond, so simply starting again is not worth avoiding.
        return len(candidate.transactions) == min(len(pending), MAX_BLOCK_TRANSACTIONS)
