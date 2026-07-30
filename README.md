# Boniato Decentralized ledger on top of py-ipv8

A P2P ledger (blockchain) built on [py-ipv8](https://github.com/Tribler/py-ipv8).
Peers generate signed dummy transactions, broadcast them across the network,
verify the signatures on receipt and store them in a **Merkle tree**. Accepted
transactions are grouped into **Merkle-committed blocks** secured by a basic
**Proof-of-Work**. It includes three gossip strategies (Push / Pull / Hybrid),
topology capture and metrics to compare them.

## Structure

```
blockchain/
├── src/blockchain/
│   ├── crypto/       asymmetric identity (keys, sign, verify)
│   ├── core/         pure domain: Transaction + Merkle tree + Block + PoW
│   ├── storage/      mempool backed by a Merkle tree
│   ├── network/      IPv8: payloads, community, gossip strategies
│   ├── metrics/      instrumentation (packets, duplicates, redundancy)
│   └── topology/     topology graph (export JSON / Graphviz DOT)
└──── simulation.py orchestrator for N peers (seeding, measurement)
```

The only dependency on IPv8 lives in `network/`; `core`, `crypto` and `storage`
are pure Python.

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

# heavier proof-of-work / bigger blocks
python scripts/mine_blocks.py --difficulty 22 --tx-per-block 8 --blocks 5
```

Prints each mined block's Merkle root, nonce, leading-zero bits and hash, then
runs a sunny-day full-chain verification and a rainy-day tamper check.

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