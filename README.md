# Boniato Decentralized ledger on top of py-ipv8

A P2P ledger (blockchain) built on [py-ipv8](https://github.com/Tribler/py-ipv8).
Peers generate signed transactions, broadcast them across the network, verify the
signatures on receipt and store them in a **Merkle tree**. Accepted transactions
are grouped into **Merkle committed blocks** secured by a basic
**Proof of Work**. It includes three gossip strategies (Push / Pull / Hybrid),
topology capture and metrics to compare them.

On top of the ledger runs the DApp it is named after: **Boniato Chain**, a
sweet potato "crop to earn" economy. Claim a plot, burn a seed to plant it, wait
for the crop to mature, harvest freshly minted **$BONI**, and spend it on more
land as the price curve climbs. Boniatos are also the **gas**, so the ecosystem
pays for its own throughput and every transaction burns supply.

And boniatos **perish**: a harvest keeps for ten days, then rots into
**fertilizer**, which can be spent to rush a growing crop. Hoarding therefore has
a price, and the supply is pushed to circulate.

## Structure

```
blockchain/
├── src/blockchain/
│   ├── crypto/       asymmetric identity (keys, sign, verify)
│   ├── core/         pure domain: Transaction + Merkle tree + Block + PoW
│   ├── storage/      mempool backed by a Merkle tree
│   ├── execution/    the DApp's state machine: economy, actions, state, engine
│   ├── access/       the chain over HTTP (stdlib only), for the web client
│   ├── network/      IPv8: payloads, community, gossip strategies
│   ├── metrics/      instrumentation (packets, duplicates, redundancy)
│   └── topology/     topology graph (export JSON / Graphviz DOT)
├── web/              React client (see web/README.md)
└──── simulation.py orchestrator for N peers (seeding, measurement)
```

The only dependency on IPv8 lives in `network/`; `core`, `crypto`, `storage`,
`execution` and `access` are pure Python, and `access` uses nothing beyond the
standard library.

`core` treats an application payload as opaque bytes and `execution` is the only
layer that interprets it, so the game can gain operations without touching
consensus, gossip or mining.

## Installation

Requires Python ≥ 3.10.

```bash
pip install -r requirements.txt          # installs pyipv8
# optional, so that `import blockchain` works without PYTHONPATH:
pip install -e .
```

## Usage

All scripts add src/ to the path automatically.

### Mine blocks: transactions → Merkle block → Proof-of-Work (Week 1 demo)

```bash
# create signed txs, group them under a Merkle root, mine PoW, verify the chain
python scripts/mine_blocks.py

# heavier proof of work / bigger blocks
python scripts/mine_blocks.py --difficulty 22 --tx-per-block 8 --blocks 5
```

Prints each mined block's Merkle root, nonce, leading zero bits and hash, then
runs a sunn day full chain verification and a rainy day tamper check.

### Play a season of Boniato Chain (the DApp)

```bash
# three farmers: plant, buy land, survive a blight, let a harvest rot,
# claim relief, spend the compost
python scripts/farm_demo.py

# heavier proof of work, slower crops
python scripts/farm_demo.py --difficulty 16 --growth-blocks 6
```

Mines a real Proof of Work chain of game transactions and narrates every block:
the grid, each crop's growth bar, pest strikes, spoilage, every farmer's larder
with its countdown to rot, and each transaction's receipt (including the rejected
ones, which still pay gas). Each block's transactions are chosen against the live
state, the way a client signs them, rather than scripted in advance.

Closes with the tokenomics ledger, the leaderboard, and two checks that matter
more than the game itself:

- **the state root**, recomputed by replaying the same chain on an independent
  node, which must match byte for byte;
- **the supply invariant**, `circulating == minted - burned - rotted`.

### Serve the chain to a browser (the access layer)

```bash
# node + HTTP API on http://127.0.0.1:8000, auto mining every 3s
python scripts/run_api.py

# no auto miner: the client controls time via POST /api/mine
python scripts/run_api.py --block-time 0
```

Then start the React client in `web/` (see `web/README.md`). The wire contract is
documented in `docs/api.md`.

Two demo affordances, stated in that document and worth repeating: the node
**holds the private keys** and signs on the client's behalf, because a browser
cannot reach IPv8's elliptic curve primitives; and it runs as a **single peer**,
so mining is local and synchronous and there is no fork choice. Keys and chain are
in memory only.

#### Design notes on the game logic

Game rules run inside consensus, so they obey stricter constraints than ordinary
application code. Three are worth calling out because breaking any of them forks
the chain rather than merely producing a bug:

- **No `random`.** Variable harvests and pest strikes are derived from SHA-256
  over consensus visible bytes, so every node rolls the same dice.
- **Entropy is the *parent* block's hash, never the block's own.** A block's hash
  contains the nonce the miner searched for, so seeding a harvest with it would
  let a miner grind nonces until their own crop paid out best.
- **Integer arithmetic only.** Floats are not bit identical across platforms;
  amounts are integers in base units and rates are basis points. Rounding
  direction is a design decision here, not an accident: see
  `crowding_scale` in `execution/economy.py` for a case where a
  too coarse floor silently inverted the incentive it was meant to create.

Block *height* is the clock, not timestamps: timestamps are miner supplied, so a
miner could otherwise claim a crop had been growing for a year. Real time enters
the chain through exactly one parameter, `blocks_per_day`, which is what turns
"boniatos keep for ten days" into a block count.

Spoilage reshapes the account model, which is the most interesting consequence in
the codebase. A balance cannot be one integer, because the chain must know *when*
each boniato was harvested, so an account holds a `Larder` of dated lots
(`execution/state.py`) — account shaped for the game's mutable facts, UTXO shaped
for the money. Two rules follow from it:

- **Spending takes the soonest to rot lot first.** Any other order would let an
  old batch spoil while newer ones are spent around it.
- **A transfer carries the age of what was spent.** Handing the recipient fresh
  boniatos would make the mechanic bypassable: bounce a spoiling batch between two
  of your own keys and it would never age.

And it creates a failure mode that needs its own answer: a farmer whose larder
rots empty cannot plant, because planting costs boniatos they no longer have. The
chain grants a destitute account exactly one cycle's worth of relief — a seed plus
the gas to plant and harvest it — which is the smallest amount that unsticks a
player without becoming an income.

### Compare the gossip strategies (Phase 3)

```bash
# report table, at 100 nodes (one TX per node spread out + settle time to converge)
python scripts/compare_strategies.py --nodes 100 --max-peers 6 --initial-connections 3 \
    --duration 30 --settle 40 --tx-interval 30 --tick-interval 1

# quick small-scale measurement (no UDP loss)
python scripts/compare_strategies.py --nodes 15 --max-peers 6 --duration 12
```

Prints the comparison table: packets sent, duplicates, redundancy ratio
and propagation coverage.

### Bring up a network and capture the topology

```bash
# ~100 peers, dense network, exporting the graph
python scripts/run_network.py --nodes 100 --strategy push --max-peers 20 \
    --duration 30 --topology-out topo.json --dot-out topo.dot

# sparse network (connected, no isolated nodes)
python scripts/run_network.py --nodes 100 --strategy pull --max-peers 3 --initial-connections 3 --duration 30

# dense / fully connected network (everyone with everyone)
python scripts/run_network.py --nodes 100 --strategy pull --max-peers 99 --initial-connections 99 --duration 35

# render the graph (requires system graphviz)
dot -Kfdp -Tpng topo.dot -o topo.png
```

### A single node (for demo / future DApp)

```bash
# node A (persistent identity in ec1.pem)
python scripts/run_node.py --key ec1.pem --port 9001 --strategy hybrid
# node B, connected to A
python scripts/run_node.py --key ec2.pem --port 9002 --connect 127.0.0.1:9001
```