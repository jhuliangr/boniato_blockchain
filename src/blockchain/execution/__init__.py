"""Execution layer: the application that runs *on* the ledger.

The layer the course slides place between consensus and access. Consensus
decides *which* blocks exist; execution decides what they *mean*. Like
:mod:`blockchain.core` it is pure Python with no IPv8 and no I/O, so the whole
game is unit-testable without a network.

The application is a sweet-potato farm, a small "crop-to-earn" economy with a
closed loop: claim a plot, burn a seed to plant, wait for the crop to grow,
harvest freshly minted $BONI, spend it on more land. Boniatos are also the gas,
so every transaction burns some supply and the economy pays for its own
throughput.

Harvested boniatos **perish**. Each batch spoils ten days after it was dug up,
and what spoils becomes **fertilizer**, which can be spent to rush a growing
crop. That single rule reaches further into the design than any other: a balance
has to become a set of dated lots (see :class:`~blockchain.execution.state.Larder`),
transfers have to carry age so spoilage cannot be laundered between keys, and the
chain needs a relief grant so a farmer whose larder rots empty is not bricked.
Hoarding now has a cost, which is the point: the supply is pushed to circulate.

Module map:

- :mod:`~blockchain.execution.economy` the rules: constants and pure math.
- :mod:`~blockchain.execution.actions` the instruction set and its wire codec.
- :mod:`~blockchain.execution.state` the world state and its commitment hash.
- :mod:`~blockchain.execution.engine` the state transition function.
"""

from blockchain.execution.actions import (
    Action,
    BuyLand,
    Claim,
    Fertilize,
    Harvest,
    Plant,
    Transfer,
    decode,
    signed,
)
from blockchain.execution.economy import BONI, BP, DEFAULT_ECONOMY, Economy
from blockchain.execution.engine import (
    BlightEvent,
    BlockContext,
    Receipt,
    RotEvent,
    StateMachine,
    SystemEvent,
)
from blockchain.execution.state import Farmland, Larder, Lot, WorldState

__all__ = [
    # rules
    "Economy",
    "DEFAULT_ECONOMY",
    "BONI",
    "BP",
    # actions
    "Action",
    "Claim",
    "Transfer",
    "BuyLand",
    "Plant",
    "Harvest",
    "Fertilize",
    "decode",
    "signed",
    # state
    "WorldState",
    "Farmland",
    "Larder",
    "Lot",
    # execution
    "StateMachine",
    "BlockContext",
    "Receipt",
    "SystemEvent",
    "BlightEvent",
    "RotEvent",
]
