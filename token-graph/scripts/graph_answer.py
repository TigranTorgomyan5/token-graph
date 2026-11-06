#!/usr/bin/env python3
"""A ranked, compact answer to a graph question.

Reuses the bundled engine's term scoring, seed choice and traversal unchanged, and replaces only
how the result is ordered and printed. The engine sorts a traversal by hop and then by degree and
prints every NODE before any EDGE, so a budget-limited answer is dominated by bare file nodes and
big utility files and holds no relationships. Here the budget goes to:

  1. the seeds the question named, then symbols ranked by hop, query relevance and low degree;
  2. at most a fair share of lines per file, so one large file cannot crowd out the others;
  3. the call/reference edges between the symbols shown, ahead of any further node.

Bare file nodes (a file with no symbol of its own in the answer) are summarised, not listed.
Language-independent: it reads only labels, locations, relations and degrees.
"""
from __future__ import annotations
import argparse
import math
import os
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "vendor"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

CHARS_PER_TOKEN = 3            # the engine's own budget convention, so --budget means the same here
STRUCTURAL = {"contains", "defines", "method"}   # parent/child links; the NODE lines already show them
EDGE_PRIORITY = {"calls": 0, "references": 1, "uses": 1, "inherits": 2, "implements": 2, "imports_from": 3}
NODE_SHARE = 0.65              # of the budget, before the edges are chosen; unused edge room goes back to nodes
FILE_SHARE = 0.20              # largest share of the node lines one file may take (seeds are exempt)
MIN_FILE_CAP = 3
ENGINE_SEEDS = 3               # of the engine's own seeds, the strongest few are kept
TOP_RANKED_SEEDS = 8           # plus this many best-scoring nodes, wherever the engine ranked them


def is_file_node(data: dict) -> bool:
    src = str(data.get("source_file") or "").replace("\\", "/")
    label = str(data.get("label") or "").replace("\\", "/")
    return bool(src and label and (src == label or src.endswith("/" + label)))


def _clean(text: object) -> str:
    return " ".join(str(text).split())[:200]


def _node_line(data: dict, nid: str) -> str:
    return f"NODE {_clean(data.get('label', nid))} [src={_clean(data.get('source_file', ''))} loc={_clean(data.get('source_location', ''))}]"


def answer(graph_path: str, question: str, budget: int = 800, contexts: list[str] | None = None,
           depth: int = 2, mode: str = "bfs") -> str:
    from graphify import serve as S
    G = S._load_graph(graph_path)
    terms = S._query_terms(question)
    qs = S._score_query(G, terms, collect_per_term_seeds=True)
    by_term = qs.best_seed_by_term
    intent = getattr(S, "_RELATIONAL_INTENT_TERMS", set())
    skipped = {t for t in by_term if t in intent}
    if skipped and any(t not in intent for t in terms):
        by_term = {t: n for t, n in by_term.items() if t not in skipped}
    engine_seeds = S._pick_seeds(qs.ranked, G=G, best_seed_by_term=by_term)
    if not engine_seeds:
        return "No matching nodes found."
    # The engine seats every query word, so a generic word ("post", "errors") seeds an unrelated node
    # and the symbol that matches several words never becomes a seed. Keep the engine's strongest
    # seeds and add the best nodes by its own IDF-weighted combined score.
    seeds = list(dict.fromkeys(engine_seeds[:ENGINE_SEEDS] + [nid for _, nid in qs.ranked[:TOP_RANKED_SEEDS]]))
    filters, _ = S._resolve_context_filters(question, contexts)
    view = S._filter_graph_by_context(S._traversal_view(G), filters)
    nodes, edges = (S._dfs if mode == "dfs" else S._bfs)(view, seeds, depth)

    relevance = {nid: score for score, nid in qs.ranked}
    seed_set = set(seeds)
    dist = {n: 0 for n in seeds if n in nodes}
    frontier, hop = list(dist), 0
    while frontier:
        hop += 1
        nxt = []
        for n in frontier:
            for nb in view.neighbors(n):
                if nb in nodes and nb not in dist:
                    dist[nb] = hop
                    nxt.append(nb)
        frontier = nxt

    def data(n: str) -> dict:
        return G.nodes[n]

    symbols = [n for n in nodes if n not in seed_set and not is_file_node(data(n))]
    symbols.sort(key=lambda n: (dist.get(n, 1 << 30), -relevance.get(n, 0.0), view.degree(n), str(n)))
    file_nodes = sum(1 for n in nodes if n not in seed_set and is_file_node(data(n)))
    ordered = [n for n in seeds if n in nodes] + symbols

    char_budget = max(budget, 1) * CHARS_PER_TOKEN
    node_chars = int(char_budget * NODE_SHARE)
    per_file_cap = max(MIN_FILE_CAP, int(node_chars / 110 * FILE_SHARE))

    def pick(limit_chars: int, start: int = 0, chosen: list[str] | None = None, used: int = 0):
        chosen = list(chosen or [])
        per_file: dict[str, int] = {}
        for n in chosen:
            per_file[str(data(n).get("source_file", ""))] = per_file.get(str(data(n).get("source_file", "")), 0) + 1
        omitted: list[str] = []
        taken = set(chosen)
        for n in ordered[start:]:
            if n in taken:
                continue
            src = str(data(n).get("source_file", ""))
            line = len(_node_line(data(n), n)) + 1
            if n not in seed_set and (per_file.get(src, 0) >= per_file_cap or used + line > limit_chars):
                omitted.append(n)
                continue
            chosen.append(n)
            taken.add(n)
            per_file[src] = per_file.get(src, 0) + 1
            used += line
        return chosen, omitted, used

    chosen, omitted, node_used = pick(node_chars)
    chosen_set = set(chosen)

    def edge_line(u: str, v: str) -> tuple[tuple, str] | None:
        raw = view[u][v]
        d = next(iter(raw.values()), {}) if view.is_multigraph() else raw
        relation = str(d.get("relation", ""))
        if relation in STRUCTURAL or not relation:
            return None
        src, tgt = d.get("_src", u), d.get("_tgt", v)
        if {src, tgt} != {u, v}:
            src, tgt = u, v
        at = f" at={_clean(d.get('source_file') or '')}:{_clean(d.get('source_location'))}" if d.get("source_location") else ""
        key = (EDGE_PRIORITY.get(relation, 4), 0 if (src in seed_set or tgt in seed_set) else 1, str(src), str(tgt))
        return key, f"EDGE {_clean(data(src).get('label', src))} --{relation}--> {_clean(data(tgt).get('label', tgt))}{at}"

    candidates, seen_pairs = [], set()
    for u, v in edges:
        if u in chosen_set and v in chosen_set and (u, v) not in seen_pairs and (v, u) not in seen_pairs:
            seen_pairs.add((u, v))
            item = edge_line(u, v)
            if item:
                candidates.append(item)
    candidates.sort(key=lambda item: item[0])
    edge_room = char_budget - node_used
    edge_lines, edge_used = [], 0
    for _, line in candidates:
        if edge_used + len(line) + 1 > edge_room:
            break
        edge_lines.append(line)
        edge_used += len(line) + 1

    # Room the edges did not use goes back to the next-best nodes.
    spare = edge_room - edge_used
    if omitted and spare > 0 and len(edge_lines) == len(candidates):
        chosen, omitted, node_used = pick(node_used + spare, 0, chosen, node_used)

    shown_files = {str(data(n).get("source_file", "")) for n in chosen}
    header = (f"Graph: {Path(graph_path).name} ({G.number_of_nodes()} nodes) | Traversal: {mode.upper()} depth={depth} | "
              f"Start: {[data(n).get('label', n) for n in seeds]} | {len(nodes)} nodes found, "
              f"{len(chosen)} symbols shown from {len(shown_files)} files, {len(edge_lines)} relations")
    out = [header, ""]
    out += [_node_line(data(n), n) for n in chosen]
    out += edge_lines
    if omitted or file_nodes:
        by_file: dict[str, int] = {}
        for n in omitted:
            by_file[str(data(n).get("source_file", ""))] = by_file.get(str(data(n).get("source_file", "")), 0) + 1
        top = ", ".join(f"{f or '?'}(+{c})" for f, c in sorted(by_file.items(), key=lambda kv: -kv[1])[:6])
        note = f"[i] Not shown: {len(omitted)} more symbols" + (f" - {top}" if top else "")
        if file_nodes:
            note += f"; {file_nodes} bare file nodes omitted"
        out += ["", note + ". Narrow with --action explain|affected --node <symbol>, or raise --budget."]
    return "\n".join(out)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("question")
    parser.add_argument("--graph", required=True)
    parser.add_argument("--budget", type=int, default=800)
    parser.add_argument("--context", action="append", default=[])
    parser.add_argument("--depth", type=int, default=2)
    parser.add_argument("--dfs", action="store_true")
    args = parser.parse_args()
    try:
        from offline_policy import install
        install()
    except Exception:  # the policy is defence in depth; this module opens no sockets
        pass
    print(answer(args.graph, args.question, args.budget, args.context, args.depth, "dfs" if args.dfs else "bfs"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
