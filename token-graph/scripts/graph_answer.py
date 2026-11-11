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

When the graph cannot tell what the question means, the answer starts with a CLARIFY block of
questions specific to it (see `clarify()`): nothing matched, the named symbol exists in several
unrelated places, or a code-like name in the question is not in the graph. The assistant asks the
user those questions instead of guessing. A clear question gets no CLARIFY block.
"""
from __future__ import annotations
import argparse
import math
import os
import re
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
CLARIFY_SCORE_RATIO = 0.9      # same-name nodes scoring at least this share of the best one are rivals
CLARIFY_LINK_HOPS = 2          # rivals this close in the graph are one place (a caller and its callee)
CLARIFY_MAX_CHOICES = 6
CLARIFY_SHORT_QUESTION = 3     # content words; a longer question names no single symbol unless code-like
CLARIFY_HEADER = "CLARIFY - the graph cannot tell what is meant. Ask the user these before changing code:"


def is_file_node(data: dict) -> bool:
    src = str(data.get("source_file") or "").replace("\\", "/")
    label = str(data.get("label") or "").replace("\\", "/")
    return bool(src and label and (src == label or src.endswith("/" + label)))


def _clean(text: object) -> str:
    return " ".join(str(text).split())[:200]


def _node_line(data: dict, nid: str) -> str:
    return f"NODE {_clean(data.get('label', nid))} [src={_clean(data.get('source_file', ''))} loc={_clean(data.get('source_location', ''))}]"


def code_names(question: str) -> list[str]:
    """Words the user typed as code: CamelCase, snake_case, dotted, called() or with digits."""
    names = []
    for raw in re.findall(r"[A-Za-z_][\w.]*(?:\(\))?", question):
        word = raw.rstrip(".")
        bare = word[:-2] if word.endswith("()") else word
        if len(bare) >= 3 and (re.search(r"[a-z][A-Z]|[A-Z]{2}[a-z]", bare) or "_" in bare.strip("_")
                               or re.search(r"\w\.\w", bare) or re.search(r"[A-Za-z]\d", bare) or word.endswith("()")):
            names.append(bare)
    return list(dict.fromkeys(names))


def base_name(data: dict) -> str:
    """The name a user would type for a node: `BetHub.PlaceBet()` -> `placebet`; a file keeps its name."""
    label = str(data.get("label") or "").strip()
    if is_file_node(data):
        return label.replace("\\", "/").rsplit("/", 1)[-1].lower()
    label = re.sub(r"\(.*\)$", "", label).strip().strip(".")
    return re.split(r"[.:#]+", label)[-1].lower() if label else ""


def _where(data: dict) -> str:
    return f"{_clean(data.get('label', ''))}  {_clean(data.get('source_file', ''))} {_clean(data.get('source_location', ''))}".rstrip()


def clarify(G, view, question: str, terms: list[str], ranked: list[tuple[float, str]]) -> list[str]:
    """Questions to ask the user when the graph cannot tell what the question means; [] when it can.

    Deliberately narrow, so a clear question is never interrupted:
      1. nothing in the graph matches the question's words;
      2. the name the question asks about exists in two or more places that are not linked within
         CLARIFY_LINK_HOPS (two services' `PlaceBet`, not a controller and the service it calls);
      3. a name the user typed as code is in no node label or file path.
    """
    questions: list[str] = []
    typed = code_names(question)
    if not ranked:
        words = ", ".join(f'"{t}"' for t in (typed or terms)) or f'"{question.strip()}"'
        return [f"Nothing in the graph matches {words}. Which class, file, service or feature is meant "
                "(the exact name, or where it lives)?"]

    texts = [(str(d.get("label") or "") + " " + str(d.get("source_file") or "")).lower() for _, d in G.nodes(data=True)]
    for name in typed:
        last = re.split(r"[.:#]+", name)[-1].lower()
        if not any(last in text for text in texts):
            questions.append(f'"{name}" is not in the graph. Is it a new thing to create, a renamed symbol, '
                             "or a config key / text outside the code? Give the existing name if there is one.")

    top_score, top = ranked[0]
    name = base_name(G.nodes[top])
    typed_lower = {re.split(r"[.:#]+", t)[-1].lower() for t in typed}
    asked_for = name in typed_lower or (
        len(terms) <= CLARIFY_SHORT_QUESTION and (name in terms or name == "".join(terms)))
    if len(name) >= 3 and asked_for:
        rivals = [nid for score, nid in ranked[:200]
                  if score >= top_score * CLARIFY_SCORE_RATIO and base_name(G.nodes[nid]) == name]
        # `BetHub.PlaceBet` or `Crashaxy.PlaceBet`: the user already said which one.
        qualifiers = [q.lower() for t in typed if re.split(r"[.:#]+", t)[-1].lower() == name
                      for q in re.split(r"[.:#]+", t)[:-1] if q]
        if qualifiers:
            narrowed = [nid for nid in rivals if all(q in (str(G.nodes[nid].get("label") or "") + " "
                                                            + str(G.nodes[nid].get("source_file") or "")).lower()
                                                     for q in qualifiers)]
            rivals = narrowed or rivals
        if len(rivals) >= 2:
            import networkx as nx
            near_view = view.to_undirected(as_view=True) if view.is_directed() else view
            places: list[tuple[str, set]] = []
            for nid in rivals:
                if any(nid in near for _, near in places):
                    continue
                near = set(nx.single_source_shortest_path_length(near_view, nid, cutoff=CLARIFY_LINK_HOPS)) \
                    if nid in near_view else {nid}
                places.append((nid, near))
            if len(places) >= 2:
                shown = places[:CLARIFY_MAX_CHOICES]
                more = f"\n     (+{len(places) - len(shown)} more)" if len(places) > len(shown) else ""
                choices = "\n".join(f"     {i}. {_where(G.nodes[nid])}" for i, (nid, _) in enumerate(shown, 1))
                label = _clean(G.nodes[top].get("label", name))
                questions.insert(0, f'"{label}" exists in {len(places)} unrelated places. Which one is meant (or all)?\n'
                                    f"{choices}{more}")
    return questions


def clarify_block(questions: list[str]) -> list[str]:
    if not questions:
        return []
    return [CLARIFY_HEADER] + [f"  {i}. {q}" for i, q in enumerate(questions, 1)] + [""]


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
        return "\n".join(["No matching nodes found.", ""] + clarify_block(clarify(G, G, question, terms, []))).rstrip()
    # The engine seats every query word, so a generic word ("post", "errors") seeds an unrelated node
    # and the symbol that matches several words never becomes a seed. Keep the engine's strongest
    # seeds and add the best nodes by its own IDF-weighted combined score.
    seeds = list(dict.fromkeys(engine_seeds[:ENGINE_SEEDS] + [nid for _, nid in qs.ranked[:TOP_RANKED_SEEDS]]))
    filters, _ = S._resolve_context_filters(question, contexts)
    view = S._filter_graph_by_context(S._traversal_view(G), filters)
    questions = clarify(G, view, question, [t for t in terms if t not in intent], qs.ranked)
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
    out = [header, ""] + clarify_block(questions)
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
