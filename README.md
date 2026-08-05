# Boniato Chain

A decentralized ledger built from scratch on [py-ipv8](https://github.com/Tribler/py-ipv8),
and a Web3 application running on top of it: a sweet-potato farming economy whose
currency **rots**.

Written for CS414 *Fundamentals of Blockchain* at Harbour.Space. Everything below
the application — transactions, Merkle trees, blocks, Proof-of-Work, gossip,
consensus, execution — is ours. The only runtime dependency is IPv8, which
provides UDP transport, peer discovery and elliptic-curve primitives.

```
transactions → merkle-committed blocks → proof-of-work → gossip → fork choice
                                                                      ↓
                                            a deterministic state machine (the game)
                                                                      ↓
                                                     HTTP API → a React client
```

**Contents** · [1. What and why](#1-what-and-why) · [2. Architecture](#2-architecture) ·
[3. Dependencies](#3-dependencies) · [4. Installation](#4-installation) ·
[5. Running it](#5-running-it) · [6. Design decisions](#6-design-decisions) ·
[7. Benchmarks](#7-benchmarks) · [8. Testing](#8-testing) ·
[9. Limitations and future work](#9-limitations-and-future-work) ·
[10. Contributors](#10-contributors)

---

## 1. What and why

### The problem

Token economies concentrate. Whoever arrives first accumulates capital, income
scales linearly with holdings, and the game ends — not because anyone cheated,
but because compounding is the only rule. Every mitigation you can bolt onto an
ordinary balance (taxes, caps, faucets) is a patch on a number that fundamentally
wants to grow.

Boniato Chain attacks this with a rule you *cannot express as a balance*:
**the money perishes.** A harvest keeps for ten days and then rots into
fertilizer. Hoarding has a cost, the supply is pushed to circulate, and the
advantage of arriving early decays on its own.

### Who the users are

Players of a crop-to-earn game. They claim a plot of land, burn a seed to plant
it, wait for the crop to mature, harvest freshly minted **$BONI**, and spend it
on more land as the price curve climbs. Boniatos are also the **gas**, so the
economy pays for its own throughput and every transaction burns supply.

### The user flow

```
1.  A player opens the client and picks a wallet (the node holds the keys — see §6).
2.  They claim a starter kit: one plot and 500 $BONI, once per account.
3.  They plant. A seed is burned; the crop matures over a fixed number of blocks.
4.  Meanwhile the chain acts on its own: pests strike a random planted plot every
    N blocks, and boniatos older than the shelf life rot into fertilizer.
5.  They harvest. The yield depends on the plot's fertility, how many neighbouring
    plots they own, any pest damage, and a dice roll derived from the parent
    block's hash.
6.  They spend: more land, or fertilizer to rush a growing crop.
7.  Every other player sees all of it — the map, the supply, the receipts —
    because it is all on the chain.
```

Watch it happen in one command: `python scripts/farm_demo.py`.

### Why a blockchain and not a database?

The honest answer first: **a database would be simpler**, and for a game with one
operator it would be enough. What it cannot give is the thing this design is
actually about.

The perishability rule is a **monetary policy**. Its entire value rests on players
believing it will not be changed for someone's benefit — that no one can quietly
mint, un-rot a favourite's larder, or reassign a plot. A database backed by an
operator asks players to trust that operator's future self. A chain does not:

- **The supply is verifiable, not asserted.** `circulating == minted − burned − rotted`
  is checked by every node on every block, and any node can recompute the whole
  history from genesis and compare state roots byte for byte.
- **Assets outlive the operator.** A plot is owned by a key, not by a row in
  somebody's table. If we shut our node down, anyone holding the chain can carry
  the world forward.
- **The rules are the code, and the code ran on everyone's machine.** A retroactive
  change is not a policy decision, it is a fork, and it is visible.

Where a chain is *not* helping: latency (a database write is microseconds; here
it is seconds), and cost (every node validates everything — see
[§7](#7-benchmarks)). We think that trade is the right one for a scarcity game
and wrong for, say, its chat feature. Being able to say which is the point.

---

## 2. Architecture

Five layers, dependencies pointing strictly downward. The IPv8 dependency is
isolated in one of them, so everything else is pure Python and unit-testable
without opening a socket.

```
┌────────────────────────────────────────────────────────────────────────────┐
│ application   web/  — React + Vite + TypeScript client                     │
└───────────────────────────────────┬────────────────────────────────────────┘
                                    │  HTTP + JSON (docs/api.md)
┌───────────────────────────────────▼────────────────────────────────────────┐
│ access        access.server · routes · node    stdlib http.server only     │
└───────────────────────────────────┬────────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────────┐
│ execution     economy · state · actions · engine                           │
│               the DApp: what a block *means*. No clock, no random, no float│
└───────────────────────────────────┬────────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────────┐
│ consensus     chain (block tree · fork choice · reorg) · validation         │
│               ledger (chain ⊕ execution) · miner                            │
│               which blocks exist, and which branch is the truth             │
└───────────────────────────────────┬────────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────────┐
│ network       community (IPv8 overlay) · gossip (Push/Pull/Hybrid)          │
│  ◀ IPv8 ▶     payloads (wire format)                                        │
└───────────────────────────────────┬────────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────────┐
│ domain        core.transaction · merkle · block · pow    crypto.identity    │
│ (pure)        storage.mempool                                               │
└────────────────────────────────────────────────────────────────────────────┘
    cross-cutting:  metrics (counters + benchmarks)   topology (graph export)
```

### What each component does

| Component | Responsibility |
| :--- | :--- |
| `crypto.Identity` | Key generation, signing, verification. The only place that knows the crypto backend. |
| `core.Transaction` | A signed intent. Carries `action` as **opaque bytes** — the domain never interprets it. |
| `core.MerkleTree` | Binary SHA-256 tree with domain separation and membership proofs. |
| `core.Block` | Immutable batch of transactions under one Merkle root, chained by `prev_hash`. |
| `core.pow` | Hashcash-style Proof-of-Work: find a nonce giving *N* leading zero bits. |
| `storage.Mempool` | Everything this node has *seen*, deduplicated, Merkle-backed. Feeds gossip. |
| `consensus.Chain` | The block **tree**: cumulative work, fork choice, reorgs, orphan pool, replay protection. |
| `consensus.Ledger` | Chain ⊕ execution. The head always has a world state; the mempool follows the head. |
| `consensus.Miner` | Proof-of-Work in bounded rounds, so a mining node still answers its peers. |
| `execution.StateMachine` | The state transition function. Deterministic by construction. |
| `execution.Economy` | The rules: integer arithmetic, basis points, hash-derived randomness. |
| `access.FarmNode` | One node's chain, mempool and demo keyring, with no HTTP in it. |
| `network.BlockchainCommunity` | The IPv8 overlay: gossips transactions, announces and serves blocks. |
| `web/` | The client. Reads one node's view over HTTP, submits player intents. |

### How components communicate

- **Peer to peer (UDP, IPv8).** Seven message types. Transactions travel by a
  swappable gossip strategy (push / pull / hybrid). Blocks are **announced by
  hash** and pulled by whoever does not recognise them — small announcements,
  no flooding. Wire format: `network/payloads.py`.
- **Client to node (HTTP, JSON).** A documented contract in
  [`docs/api.md`](docs/api.md); both sides are written against it and neither
  invents endpoints.
- **Between layers (function calls).** Downward only. `network` is the sole
  importer of IPv8; `access` uses nothing beyond the standard library.

**One path in, for everything.** A block mined locally and a block arriving from
a peer go through the same `Ledger.connect()` — same validation, same fork
choice. The HTTP node and the P2P node are two shells around one ledger, so they
cannot drift apart in how a block is applied.

---

## 3. Dependencies

| | Version | Why |
| :--- | :--- | :--- |
| **Python** | ≥ 3.10 | `match`-free but uses PEP 604 unions and modern dataclasses |
| **pyipv8** | 3.2.1 | UDP transport, peer discovery, elliptic-curve keys. **The only runtime dependency.** |
| Node.js + npm | ≥ 18 | Only for the web client (`web/`). The chain runs without it. |
| Graphviz | any | Optional. Renders exported topology graphs to images. |
| coverage | any | Optional, development only. For the numbers in [§8](#8-testing). |

Everything else — hashing, Merkle trees, the HTTP server, the test runner — is
the Python standard library.

---

## 4. Installation

```bash
git clone <this-repo>
cd blockchain

python3 -m venv .venv && source .venv/bin/activate   # recommended
pip install -r requirements.txt                      # installs pyipv8, and nothing else
```

Optional but convenient — makes `import blockchain` work without setting
`PYTHONPATH`:

```bash
pip install -e .
```

**Verify the install:**

```bash
python -m unittest discover -s tests -t .     # 342 tests, ~7 s
# without `pip install -e .`, prefix with PYTHONPATH=src
```

The scripts in `scripts/` add `src/` to the path themselves, so they work either
way.

For the web client:

```bash
cd web && npm install
```

---

## 5. Running it

Five entry points, in the order they are worth looking at.

### 5.1 The DApp, narrated — `farm_demo.py`

The fastest way to see the whole system. Mines a real Proof-of-Work chain of game
transactions and narrates every block: the grid, each crop's growth bar, pest
strikes, spoilage, every farmer's larder with its countdown to rot, and each
transaction's receipt — including the rejected ones, which still pay gas.

```bash
python scripts/farm_demo.py
python scripts/farm_demo.py --difficulty 16 --growth-blocks 6    # slower crops, heavier PoW
```

It closes with the two checks that matter more than the game:

```
═══ consensus check ═══
  node A state root  1810854740c5213c5bf3afa41109d15701569e43d296de290135cde931d94b54
  node B state root  1810854740c5213c5bf3afa41109d15701569e43d296de290135cde931d94b54
  independent replay agrees: PASS
  supply invariant (circulating == minted - burned - rotted): PASS
```

### 5.2 The multi-node chain — `run_chain.py`

A fleet of peers mining against each other over IPv8, which is where consensus
earns its name. Four phases: convergence, a node joining late and synchronising
from genesis, a heavier branch forcing a reorganisation, and forged blocks being
rejected.

```bash
python scripts/run_chain.py                                  # 8 peers, 3 mining, ~90 s
python scripts/run_chain.py --nodes 10 --miners 8 --difficulty 8   # forks on purpose
```

```
═══ reorganisation: a heavier branch arrives ═══
  mined 5 blocks forking 2 blocks back, and offered them to node-000
  node-000: height 4 -> 7, undid 2 block(s), 24 transaction(s) back in the mempool
  network followed: 100% of nodes on 000029ce982970c5 at height 7

═══ rainy day: forged blocks offered to a live peer ═══
  no proof-of-work                   -> invalid   insufficient proof-of-work (needs 16 leading zero bits)
  merkle root does not match         -> invalid   merkle root does not commit to these transactions
  replays a confirmed transaction    -> invalid   replays a transaction from an ancestor block
  head unchanged after all three: PASS

  all nodes on one chain:        PASS
  all nodes on one world state:  PASS
```

### 5.3 The web client — `run_api.py` + `web/`

```bash
# terminal 1 — a node with an HTTP API on :8000, auto-mining every 3 s
python scripts/run_api.py

# terminal 2 — the client on :5173
cd web && npm run dev
```

Then open <http://127.0.0.1:5173>. Use `--block-time 0` to disable the auto-miner
and drive time yourself with the **Mine block** button.

![The Boniato Chain client](docs/client.png)

The panel worth staring at is **the larder**: a balance here is not a number but a
queue of dated lots, each with its own expiry and a freshness bar. Spending
always drains the one nearest to rotting. The **activity feed** shows rejected
transactions too, with the chain's reason — because a failed action still burns
gas, and seeing *why* the chain said no is most of what makes it legible.

Client details: [`web/README.md`](web/README.md). Wire contract: [`docs/api.md`](docs/api.md).

### 5.4 Blocks and Proof-of-Work in isolation — `mine_blocks.py`

```bash
python scripts/mine_blocks.py --difficulty 22 --tx-per-block 8 --blocks 5
```

Prints each block's Merkle root, nonce, leading zero bits and hash, then runs a
full-chain verification and a tamper check.

### 5.5 The gossip experiments — `run_network.py`, `compare_strategies.py`

~100 peers, topology capture, and the Push / Pull / Hybrid comparison.

```bash
python scripts/compare_strategies.py --nodes 100 --max-peers 6 --duration 30 --settle 40

python scripts/run_network.py --nodes 100 --strategy pull --max-peers 3 \
    --initial-connections 3 --duration 35 --topology-out topo.json --dot-out topo.dot
dot -Kfdp -Tpng topo.dot -o topo.png     # needs system graphviz
```

Results and analysis: [`docs/design-and-analysis.md`](docs/design-and-analysis.md).

### 5.6 Benchmarks

```bash
python scripts/benchmark.py --json docs/benchmarks.json   # the network, ~11 min
python scripts/microbench.py                              # the primitives, ~30 s
```

---

## 6. Design decisions

The decisions we would defend in a review, and the reasoning that produced them.

### 6.1 Game logic runs inside consensus, so it obeys stricter rules

Three constraints, each of which **forks the chain** rather than merely producing
a bug. They are why `execution/` looks the way it does.

**No `random`.** Harvest sizes and pest targets are SHA-256 over consensus-visible
bytes, so every node rolls the same dice.

**Entropy is the *parent* block's hash, never the block's own.** A block's hash
contains the nonce the miner searched for. Seeding a harvest with it would let a
miner grind nonces until their own crop paid out best — a self-inflicted MEV
that is invisible until you look for it.

**Integer arithmetic only.** Floats are not bit-identical across platforms.
Amounts are integers in base units; rates are basis points.

**Block height is the clock, not timestamps.** Timestamps are miner-supplied, so
a miner could otherwise claim a crop had been growing for a year. Real time
enters the chain through exactly one parameter, `blocks_per_day`.

### 6.2 Perishability reshaped the account model

The most interesting consequence in the codebase. A balance cannot be one
integer, because the chain must know *when* each boniato was harvested. So an
account holds a **larder of dated lots** — account-shaped for the game's mutable
facts, UTXO-shaped for the money. Two rules follow, and neither is optional:

- **Spending takes the soonest-to-rot lot first.** Any other order would let an
  old batch spoil while newer ones are spent around it.
- **A transfer carries the age of what was spent.** Handing the recipient fresh
  boniatos would make the mechanic bypassable: bounce a spoiling batch between two
  of your own keys and it would never age.

That in turn creates a failure mode needing its own answer: a farmer whose larder
rots empty cannot plant, because planting costs boniatos they no longer have. The
chain grants a destitute account exactly one cycle's worth of relief — the
smallest amount that unsticks a player without becoming an income.

### 6.3 Fork choice is by cumulative work, not chain length

Each block contributes `2**difficulty` units. With the fixed difficulty this
project uses, the heaviest branch is always the longest one, so the two rules
coincide *today*. They stop coinciding the moment difficulty retargets, and
picking the longest chain then lets an attacker win with a long branch of cheap
blocks. Expressing the rule as work costs nothing now and is the rule that is
actually correct.

**Ties go to the incumbent.** A block that merely equals the head's work does not
replace it. Without that, two peers holding the same two blocks could flap
between them forever and nothing would ever confirm.

### 6.4 A transaction may not be replayed on the same branch

A signed transaction is a bearer instrument: nothing inside it names the block it
belongs to, so a miner who copies one out of an old block could execute it twice.
A block is therefore invalid if it carries a transaction already present in one
of its **ancestors** — ancestors, not "the chain", because a transaction spent on
the branch we happen to follow is legitimately unspent on a competing one.

We walk the branch to enforce it, which is `O(height)` per block. Production
chains do not pay that: Bitcoin's UTXO set makes a spent output disappear, and
Ethereum's per-account nonce makes a replayed transaction unorderable. Both
replace the walk with an `O(1)` lookup against state they already maintain. Ours
is affordable at this scale and obviously correct, which is the trade we chose.

### 6.5 Reorganisation replays from genesis

When a competing branch wins, the state built on the old one is wrong. Undoing a
block in place would be faster, but the execution layer has no inverse: spoilage
destroys lots, a harvest mints against a hash-derived yield, and reconstructing
what a block consumed means keeping an undo log for every one of them — which is
exactly what Bitcoin does, and exactly the complexity we did not want in the last
week. `Ledger.rebuild_cost()` reports the price so it is a measured trade rather
than an adjective.

### 6.6 Blocks are announced, not flooded

Phase 3 measured push, pull and hybrid gossip across sparse and dense topologies
and found eager flooding to be the worst option at scale — catastrophically so on
a dense graph, where push spends ~994 packets per node to reach 10 % of the
network. When blocks arrived, we applied our own result: a node announces the
*hash* of its new head (40 bytes) and peers that do not recognise it ask for the
block. That is the shape Bitcoin uses for `inv`/`getdata`; we arrived at it from
our own numbers.

### 6.7 Mining in bounded rounds, and the nonce that must not reset

A node that disappears into a hash loop stops answering its peers. `Miner.step()`
tries a fixed number of nonces and returns, so mining interleaves with gossip.
Hashes-per-round × rounds-per-second *is* the node's hash rate, which makes
unequal mining power a configuration rather than a code change.

One detail is load-bearing: **the nonce counter is never reset**, not even when
the block template is rebuilt. Trials are independent, so a rebuild loses no
progress — but resetting to zero would be quietly fatal. With transactions
arriving faster than blocks are found, the miner would rebuild constantly and
spend eternity re-testing the same low nonces without ever completing a search.
We hit exactly this while tuning the demo. Real miners avoid the same trap by
rolling an extranonce.

### 6.8 Two deliberate demo affordances, stated so they are not mistaken for design

**The node holds the private keys.** Signing needs IPv8's elliptic-curve
primitives, which a browser cannot reach, so the node signs on the client's
behalf. That makes it a **custodial wallet** — wrong in production, and called out
at the top of `docs/api.md`. A real DApp signs in the browser and posts an
already-signed transaction, which `FarmNode.accept()` would take unchanged.

**The chain lives in memory.** Restarting a node starts a new world. Persistence
is in [§9](#9-limitations-and-future-work).

---

## 7. Benchmarks

Full method, tables and caveats: **[`docs/benchmarks.md`](docs/benchmarks.md)**.
Raw data: [`docs/benchmarks.json`](docs/benchmarks.json). Headlines:

| question | answer |
| :--- | :--- |
| **Capacity** | **~11 tx/s** (4 nodes, difficulty 14). Below it, latency is flat; above it, work queues. |
| **Inclusion latency** | 1–2 s below capacity, 13 s above it |
| **Confirmation at depth 6** | 7.5× the inclusion latency — latency and security are one knob, not two |
| **Block propagation** | 39 ms for a near-empty block, **5.4 s for a full one** |
| **Safe difficulty** | ≥ 14 here. At 12, **52 % of mined blocks are wasted** and the network never converges. |
| **Scaling** | Flat from 2 to 8 nodes, as full replication requires. Cost per node rises 3.5×. |
| **Bottleneck** | **Signature verification (1.75 ms), not Proof-of-Work (0.63 µs)** |

Two findings we did not expect:

**Proof-of-Work is the cheapest thing a node does per transaction.** Mining at the
demo's rate costs ~15 ms of CPU per second; verifying the transactions arriving in
that same second costs ~350 ms. The operation the course frames as deliberately
expensive is not what makes this system slow — the 409-bit curve we inherited from
IPv8's default is. The same library offers curve25519: 12× faster verification and
40 % smaller transactions.

**Block size drives the fork rate, and we measured the chain.** As load pushes
blocks from 6 to 23 transactions, propagation goes from 39 ms to 5.4 s and the
share of wasted blocks climbs from 0 % to 47 %. That is `Δ ∝ B` from the lecture,
observed in our own system rather than quoted from it.

---

## 8. Testing

```bash
python -m unittest discover -s tests -t .     # 342 tests, ~7 s
                                              # PYTHONPATH=src if not pip-installed
python -m coverage run --source=src/blockchain -m unittest discover -s tests -t .
python -m coverage report
```

**342 tests, 93 % statement coverage.** The suite runs in seven seconds, which is
deliberate: a test suite nobody waits for is a test suite nobody runs.

| layer | statements | coverage | what is tested |
| :--- | ---: | ---: | :--- |
| `execution` | 638 | **99 %** | every action, the supply invariant, spoilage order, rounding |
| `consensus` | 374 | **96 %** | fork choice, reorgs, orphans, replay, mining |
| `access` | 308 | 84 % | every endpoint, error paths, serialization |
| `network` | 266 | 88 % | payload round-trips, gossip strategies, live consensus |
| `core` | 187 | 97 % | Merkle proofs, tamper detection, PoW |
| `metrics` | 160 | 96 % | the benchmark maths itself |
| `crypto` | 37 | **100 %** | sign, verify, reject |
| `storage` | 37 | 92 % | dedup, Merkle root consistency |
| **total** | **2 183** | **93 %** | |

### What kind of testing

- **Unit tests** for every pure layer. The domain has no IPv8 dependency, so a
  fork, a reorganisation or a replayed transaction can all be provoked
  deterministically without opening a socket.
- **Adversarial tests.** Blocks with no Proof-of-Work, a Merkle root that does not
  commit to its transactions, a forged signature, a transaction included twice in
  one block, a transaction replayed from an ancestor, a timestamp that goes
  backwards. Each is rejected with a stated reason.
- **Integration tests over real IPv8** (`test_consensus_network.py`): peers on
  loopback that must agree by talking to each other. Catches what unit tests
  cannot — announcements never sent, blocks never served, orphans never resolved.
- **Property checks in the demos.** `farm_demo.py` replays its chain on an
  independent node and asserts the state roots match byte for byte, then asserts
  the supply invariant. `run_chain.py` asserts every node ends on one head *and*
  one state root.
- **Tests for the measurement code.** A wrong benchmark is worse than no
  benchmark, because it gets quoted.

### Assumptions the tests rest on

1. **Determinism of the execution layer.** Everything else follows from it. It is
   checked directly by independent replay rather than assumed.
2. **Honest majority of hash power.** We implement the fork-choice rule; we do not
   test a 51 % attack.
3. **Clocks are irrelevant.** Enforced, not assumed: height is the only clock, and
   the sole timestamp rule is that it must not go backwards.
4. **Loopback is the network.** The weakest assumption, and the one
   [`docs/benchmarks.md`](docs/benchmarks.md) §5 spends the most words on.

---

## 9. Limitations and future work

Stated plainly, worst first.

| Limitation | Consequence | Fix |
| :--- | :--- | :--- |
| **Custodial wallet** | The node can spend a player's funds | Sign in the browser (WASM crypto); the node already accepts pre-signed transactions |
| **No persistence** | Restarting a node loses the chain and every key | Write blocks to SQLite; the state is already rebuildable by replay |
| **No difficulty retargeting** | Block time drifts with total hash power | Retarget on a sliding window; the fork choice is already work-based, so nothing else changes |
| **32 transactions per block** | Caps throughput; a full block is a 9 KB datagram | Compact blocks (BIP 152): announce transaction ids, let peers rebuild from their mempool |
| **sect409k1 signatures** | 1.75 ms to verify — the measured bottleneck | curve25519: 12× faster, 40 % smaller. One-line default change plus a new genesis |
| **Reorg replays from genesis** | `O(height)` per reorganisation | Per-block undo logs |
| **No state root in the header** | Nodes can *agree* on state but cannot *prove* it | Commit the state root in the block header |
| **Backward sync, one block per round trip** | A late joiner needs `O(depth)` exchanges | Headers-first sync with a block locator |
| **Single-process simulation** | The 16-node benchmark measures our harness, not the protocol | Run peers as separate processes or hosts |
| **Orphan pool is memory** | Bounded at 256 blocks, oldest dropped | Adequate; noted for completeness |

Beyond fixes, the two directions we would take next: **light clients**, which the
Merkle proofs already support but nothing uses; and **a real fee market**, since
gas is currently a flat rate and so blocks have no ordering incentive.

---

## 10. Contributors

CS414 Fundamentals of Blockchain — Harbour.Space, 2026.

| | Main areas |
| :--- | :--- |
| **Jhulian Garcia** | Proof-of-Work, consensus layer, React client, API contract, repository |
| **Alberto Leyva Guerra** | Execution layer (economy, state, actions, engine), HTTP access layer, demo scripts |
| **Abhinav Siddharth** | Test suite for the execution and access layers |
| **Dishant Poudel** | Tests and QA on the state and economy modules |

### Sources

- Nakamoto, *Bitcoin: A Peer-to-Peer Electronic Cash System* (2008) — Proof-of-Work,
  hash chaining, longest/heaviest chain.
- Back, *Hashcash — A Denial of Service Counter-Measure* (2002) — the leading-zero-bits
  proof.
- Bitcoin BIP 152, *Compact Block Relay* — the fix for our block-size cap.
- Bitcoin Core's move from `nHeight` to `nChainWork` — why fork choice is by work.
- The [py-ipv8 documentation](https://py-ipv8.readthedocs.io/) — keys, signatures,
  overlays, community identifiers.
