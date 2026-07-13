from __future__ import annotations

import math
from dataclasses import dataclass

import networkx as nx  # type: ignore[import-untyped]

from raytsystem.codegraph.contracts import CodeCommunity, CodeEdge, CodeNode, CodeNodeKind


@dataclass(frozen=True)
class ClusteredGraph:
    nodes: tuple[CodeNode, ...]
    communities: tuple[CodeCommunity, ...]


def _simple_graph(nodes: tuple[CodeNode, ...], edges: tuple[CodeEdge, ...]) -> nx.Graph:
    graph = nx.Graph()
    graph.add_nodes_from(node.node_id for node in nodes)
    for edge in edges:
        if graph.has_edge(edge.source, edge.target):
            graph[edge.source][edge.target]["weight"] += 1
        else:
            graph.add_edge(edge.source, edge.target, weight=1)
    return graph


def _community_label(members: tuple[str, ...], by_id: dict[str, CodeNode]) -> str:
    eligible = [
        by_id[node_id]
        for node_id in members
        if by_id[node_id].kind
        not in {
            CodeNodeKind.DEPENDENCY,
            CodeNodeKind.DIRECTORY,
            CodeNodeKind.FILE,
            CodeNodeKind.REPOSITORY,
        }
    ]
    pool = eligible or [by_id[node_id] for node_id in members]
    pool.sort(key=lambda node: (-len(node.qualified_name), node.label.casefold(), node.node_id))
    return pool[0].label


def _cohesion_ppm(graph: nx.Graph, members: tuple[str, ...]) -> int:
    member_set = set(members)
    incident = 0
    internal = 0
    for source, target in graph.edges:
        source_inside = source in member_set
        target_inside = target in member_set
        if source_inside or target_inside:
            incident += 1
            if source_inside and target_inside:
                internal += 1
    return 1_000_000 if incident == 0 else round(internal * 1_000_000 / incident)


def cluster_graph(nodes: tuple[CodeNode, ...], edges: tuple[CodeEdge, ...]) -> ClusteredGraph:
    if not nodes:
        return ClusteredGraph(nodes=(), communities=())
    graph = _simple_graph(nodes, edges)
    raw = nx.community.louvain_communities(graph, weight="weight", seed=42)
    ordered_members = sorted(
        (tuple(sorted(community)) for community in raw),
        key=lambda members: (members[0], len(members), members),
    )
    by_id = {node.node_id: node for node in nodes}
    community_by_node: dict[str, int] = {}
    communities: list[CodeCommunity] = []
    for community_id, members in enumerate(ordered_members):
        for node_id in members:
            community_by_node[node_id] = community_id
        communities.append(
            CodeCommunity(
                community_id=community_id,
                label=_community_label(members, by_id),
                node_ids=members,
                cohesion_ppm=_cohesion_ppm(graph, members),
            )
        )

    eligible = [
        node
        for node in nodes
        if node.kind not in {CodeNodeKind.FILE, CodeNodeKind.DIRECTORY, CodeNodeKind.REPOSITORY}
    ]
    god_count = min(10, max(1, math.isqrt(len(eligible)))) if eligible else 0
    god_ids = {
        node.node_id
        for node in sorted(
            eligible,
            key=lambda node: (-graph.degree[node.node_id], node.label.casefold(), node.node_id),
        )[:god_count]
        if graph.degree[node.node_id] >= 2
    }
    if graph.number_of_nodes() <= 1:
        centrality: dict[str, float] = {node.node_id: 0.0 for node in nodes}
    elif graph.number_of_nodes() <= 1_000:
        centrality = nx.betweenness_centrality(graph, normalized=True)
    else:
        centrality = nx.betweenness_centrality(
            graph,
            k=min(100, graph.number_of_nodes()),
            normalized=True,
            seed=42,
        )
    bridge_candidates = [
        node
        for node in eligible
        if len({community_by_node.get(neighbor) for neighbor in graph.neighbors(node.node_id)}) > 1
    ]
    bridge_ids = {
        node.node_id
        for node in sorted(
            bridge_candidates,
            key=lambda node: (
                -centrality.get(node.node_id, 0.0),
                node.label.casefold(),
                node.node_id,
            ),
        )[:10]
        if centrality.get(node.node_id, 0.0) > 0
    }
    rendered = tuple(
        sorted(
            (
                node.model_copy(
                    update={
                        "community_id": community_by_node[node.node_id],
                        "is_god": node.node_id in god_ids,
                        "is_bridge": node.node_id in bridge_ids,
                    }
                )
                for node in nodes
            ),
            key=lambda node: node.node_id,
        )
    )
    return ClusteredGraph(nodes=rendered, communities=tuple(communities))
