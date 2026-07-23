"""An undirected topology graph with export + statistics.

Kept generic and dependency-free: nodes are opaque string ids and edges are
undirected. The harness feeds it each node's neighbour list (derived from
``community.get_peers()``); this module only knows about graph structure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class Topology:
    """Undirected graph of peers and their connections."""

    nodes: set[str] = field(default_factory=set)
    # Canonical undirected edges stored as sorted (a, b) tuples.
    edges: set[tuple[str, str]] = field(default_factory=set)

    # -- construction ---------------------------------------------------------

    def add_node(self, node_id: str) -> None:
        self.nodes.add(node_id)

    def add_edge(self, a: str, b: str) -> None:
        if a == b:
            return  # ignore self-loops
        self.nodes.add(a)
        self.nodes.add(b)
        self.edges.add((a, b) if a <= b else (b, a))

    @classmethod
    def from_adjacency(cls, adjacency: dict[str, list[str]]) -> "Topology":
        """Build from a ``node_id -> [neighbour_id, ...]`` mapping.

        Connections are treated as undirected: if A lists B *or* B lists A, an
        edge A-B exists. This matches IPv8, where a verified peer link is
        mutual once introduction completes.
        """
        topo = cls()
        for node_id, neighbours in adjacency.items():
            topo.add_node(node_id)
            for neighbour in neighbours:
                topo.add_edge(node_id, neighbour)
        return topo

    # -- statistics -----------------------------------------------------------

    def degrees(self) -> dict[str, int]:
        degree = {node: 0 for node in self.nodes}
        for a, b in self.edges:
            degree[a] += 1
            degree[b] += 1
        return degree

    def stats(self) -> dict:
        degree = self.degrees()
        values = list(degree.values()) or [0]
        n = len(self.nodes)
        return {
            "nodes": n,
            "edges": len(self.edges),
            "min_degree": min(values),
            "max_degree": max(values),
            "avg_degree": round(sum(values) / n, 2) if n else 0.0,
            "isolated_nodes": sum(1 for d in values if d == 0),
        }

    # -- export ---------------------------------------------------------------

    def to_json(self) -> str:
        degree = self.degrees()
        payload = {
            "nodes": [{"id": n, "degree": degree[n]} for n in sorted(self.nodes)],
            "edges": [{"source": a, "target": b} for a, b in sorted(self.edges)],
            "stats": self.stats(),
        }
        return json.dumps(payload, indent=2)

    def to_dot(self) -> str:
        """Graphviz DOT source (render with ``dot -Tpng topo.dot -o topo.png``)."""
        lines = ["graph topology {", "  node [shape=circle, fontsize=8];"]
        for node in sorted(self.nodes):
            lines.append(f'  "{node}";')
        for a, b in sorted(self.edges):
            lines.append(f'  "{a}" -- "{b}";')
        lines.append("}")
        return "\n".join(lines)
