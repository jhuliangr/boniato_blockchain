"""Network topology capture and export (Phase 3).

Turns a live set of nodes into a graph of "who is connected to whom", computes
degree statistics, and exports to JSON and Graphviz DOT both dependency-free
so the visualisation works without networkx/matplotlib installed.
"""

from blockchain.topology.graph import Topology

__all__ = ["Topology"]
