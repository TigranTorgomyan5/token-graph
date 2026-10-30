#!/usr/bin/env python3
"""Answer a question / path / impact request on a project graph for the Project Explorer.

Runs the bundled engine's own query, shortest-path and affected functions (the
same ones /token-graph uses) under the offline policy, and prints JSON with the
answer text plus the node ids to highlight. Usage:

    ask_engine.py <project> query    <question> [budget] [--no-tests] [--no-generated]
    ask_engine.py <project> path     <source> <target>  [--no-tests] [--no-generated]
    ask_engine.py <project> affected <node> [depth]     [--no-tests] [--no-generated]
"""
from __future__ import annotations
import json
from pathlib import Path
import re
import sys

SCRIPTS = Path(__file__).resolve().parent
sys.path[:0] = [str(SCRIPTS), str(SCRIPTS.parent / "vendor")]
from offline_policy import install  # noqa: E402

install(False)

import networkx as nx  # noqa: E402
from graphify import affected as gaffected  # noqa: E402
from graphify import serve  # noqa: E402

QUERY_DEPTH = 2  # shallower than the CLI default (3): a highlight of thousands of nodes shows nothing


def levels_from(G: nx.Graph, seeds: list[str], allowed: set[str]) -> dict[str, int]:
    level = {s: 0 for s in seeds}
    frontier = list(seeds)
    while frontier:
        nxt = []
        for n in frontier:
            for m in G.neighbors(n):
                if m in allowed and m not in level:
                    level[m] = level[n] + 1
                    nxt.append(m)
        frontier = nxt
    return level


def ask_query(G: nx.Graph, graph_file: str, question: str, budget: int) -> dict:
    # Mirrors serve._query_graph_text so the highlighted nodes are the ones the text describes.
    qs = serve._score_query(G, serve._query_terms(question), collect_per_term_seeds=True)
    by_term = qs.best_seed_by_term
    intent = {t for t in by_term if t in serve._RELATIONAL_INTENT_TERMS}
    if intent and any(t not in serve._RELATIONAL_INTENT_TERMS for t in serve._query_terms(question)):
        by_term = {t: nid for t, nid in by_term.items() if t not in intent}
    seeds = serve._pick_seeds(qs.ranked, G=G, best_seed_by_term=by_term)
    if not seeds:
        return {"text": "No matching nodes found. Try a class, method or file name.", "nodes": {}, "seeds": []}
    filters, _ = serve._resolve_context_filters(question, None)
    traversal = serve._filter_graph_by_context(serve._traversal_view(G), filters)
    nodes, _edges = serve._bfs(traversal, seeds, QUERY_DEPTH)
    text = serve._query_graph_text(G, question, depth=QUERY_DEPTH, token_budget=budget, graph_path=graph_file)
    # The text keeps only what fits the token budget; highlight exactly those NODE lines, not the whole traversal.
    by_key = {}
    for nid in nodes:
        data = G.nodes[nid]
        by_key.setdefault((str(data.get("label", nid)), str(data.get("source_file") or ""), str(data.get("source_location") or "")), nid)
    shown = set(seeds)
    for match in re.finditer(r"^NODE (.*?) \[src=(.*?) loc=(.*?) community=", text, re.M):
        nid = by_key.get(match.groups())
        if nid:
            shown.add(nid)
    levels = levels_from(traversal, seeds, nodes)
    return {"text": text, "nodes": {n: levels.get(n, QUERY_DEPTH) for n in shown}, "seeds": seeds, "traversed": len(nodes)}


def ask_path(G: nx.Graph, source: str, target: str) -> dict:
    ends = []
    for role, query in (("source", source), ("target", target)):
        nid, _scored, error = serve._resolve_path_endpoint(G, query)
        if error:
            return {"text": error, "nodes": {}, "seeds": [], "ambiguous": role}
        if nid is None:
            return {"text": f"No node matching {role} '{query}' found.", "nodes": {}, "seeds": []}
        ends.append(nid)
    if ends[0] == ends[1]:
        return {"text": f"'{source}' and '{target}' are the same node. Pick two different nodes.", "nodes": {}, "seeds": []}
    args = {"source": ends[0], "target": ends[1], "max_hops": 12}
    text = serve._shortest_path_text(G, args)
    undirected = text.startswith("No directed path")
    if undirected:
        # No caller→callee route: fall back to any connection, and say so.
        args["undirected"] = True
        text = "No path following call/import direction; showing the shortest connection ignoring direction.\n" + serve._shortest_path_text(G, args)
    try:
        if undirected:
            U = nx.Graph()
            U.add_nodes_from(sorted(G.nodes))
            U.add_edges_from(sorted((min(u, v), max(u, v)) for u, v in G.edges()))
            route = nx.shortest_path(U, ends[0], ends[1])
        else:
            D = nx.DiGraph()
            D.add_nodes_from(sorted(G.nodes))
            D.add_edges_from(sorted((d.get("_src", u), d.get("_tgt", v)) for u, v, d in G.edges(data=True)))
            D.add_edges_from(sorted((d.get("_tgt", v), d.get("_src", u)) for u, v, d in G.edges(data=True) if d.get("relation") == "contains"))
            route = nx.shortest_path(D, ends[0], ends[1])
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        route = []
    return {"text": text, "nodes": {n: i for i, n in enumerate(route)}, "seeds": ends, "route": route}


def ask_affected(G: nx.Graph, root: Path, node: str, depth: int) -> dict:
    seed = gaffected.resolve_seed(G, node, root)
    if seed is None:
        rivals = serve.find_node_ambiguity(G, node)
        text = serve._ambiguity_message(G, node, rivals) if rivals else f"No unique node matches '{node}'. Use a more exact name or the node id."
        return {"text": text, "nodes": {}, "seeds": [], "ambiguous": "node" if rivals else None}
    hits = gaffected.affected_nodes(G, seed, depth=depth)
    text = gaffected.format_affected(G, seed, depth=depth, root=root)
    nodes = {seed: 0}
    nodes.update({h.node_id: h.depth for h in hits})
    return {"text": text, "nodes": nodes, "seeds": [seed]}


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__, file=sys.stderr)
        return 2
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    project, mode, *args = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = Path(project).resolve()
    from node_filters import scoped_graph
    graph_file = scoped_graph(root / "graphify-out" / "graph.json", tests="--no-tests" not in flags, generated="--no-generated" not in flags)
    if mode == "affected":
        G = gaffected.load_graph(graph_file)
    else:
        G = serve._load_graph(str(graph_file))
    if mode == "query":
        result = ask_query(G, str(graph_file), args[0], int(args[1]) if len(args) > 1 else 800)
    elif mode == "path":
        result = ask_path(G, args[0], args[1])
    elif mode == "affected":
        result = ask_affected(G, root, args[0], int(args[1]) if len(args) > 1 else 2)
    else:
        print(f"unknown mode {mode}", file=sys.stderr)
        return 2
    result["mode"] = mode
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
