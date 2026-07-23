# Harbour Space — Decentralized ledger on top of py-ipv8

A P2P ledger (blockchain) built on [py-ipv8](https://github.com/Tribler/py-ipv8).
Peers generate signed dummy transactions, broadcast them across the network,
verify the signatures on receipt and store them in a **Merkle tree**. It includes
three gossip strategies (Push / Pull / Hybrid), topology capture and
metrics to compare them.

## Structure

```
blockchain/
├── src/blockchain/
│   ├── crypto/       asymmetric identity (keys, sign, verify)
│   ├── core/         pure domain: Transaction + Merkle tree
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