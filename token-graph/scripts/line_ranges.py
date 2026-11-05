"""Add the last line of each code symbol (`end_line`) to graph.json.

The extractors record only the line a symbol starts on. With the end line an answer
can say `L1200-1236`, so an assistant reads exactly one function instead of guessing
how much of the file to open. Every file is parsed once with the Tree-sitter grammar
already installed for the engine; files in other languages simply get no end line.

Run with the engine's Python:  python line_ranges.py <graph.json> <project root>
"""
from __future__ import annotations

import importlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

# extension -> (grammar module, language function)
GRAMMARS: dict[str, tuple[str, str]] = {
    ".py": ("tree_sitter_python", "language"), ".pyi": ("tree_sitter_python", "language"),
    ".js": ("tree_sitter_javascript", "language"), ".jsx": ("tree_sitter_javascript", "language"),
    ".mjs": ("tree_sitter_javascript", "language"), ".cjs": ("tree_sitter_javascript", "language"),
    ".ts": ("tree_sitter_typescript", "language_typescript"), ".mts": ("tree_sitter_typescript", "language_typescript"),
    ".cts": ("tree_sitter_typescript", "language_typescript"), ".tsx": ("tree_sitter_typescript", "language_tsx"),
    ".java": ("tree_sitter_java", "language"), ".cs": ("tree_sitter_c_sharp", "language"),
    ".go": ("tree_sitter_go", "language"), ".rs": ("tree_sitter_rust", "language"),
    ".kt": ("tree_sitter_kotlin", "language"), ".kts": ("tree_sitter_kotlin", "language"),
    ".rb": ("tree_sitter_ruby", "language"), ".php": ("tree_sitter_php", "language_php"),
    ".c": ("tree_sitter_c", "language"), ".h": ("tree_sitter_c", "language"),
    ".cpp": ("tree_sitter_cpp", "language"), ".cc": ("tree_sitter_cpp", "language"),
    ".cxx": ("tree_sitter_cpp", "language"), ".hpp": ("tree_sitter_cpp", "language"),
    ".scala": ("tree_sitter_scala", "language"), ".swift": ("tree_sitter_swift", "language"),
    ".lua": ("tree_sitter_lua", "language"),
}

# Node types that declare a symbol, across grammars (function_definition, method_declaration,
# class_declaration, function_item, impl_item, struct_specifier, lexical_declaration ...).
DEFINITION = re.compile(
    r"declaration|definition|function|method|constructor|class|interface|struct|enum|record"
    r"|trait|impl|module|object|item|specifier"
)
# Attributes/decorators may put a declaration's first row a few rows above its name.
MAX_LEAD_ROWS = 3
START = re.compile(r"L(\d+)$")


def _parser(suffix: str, cache: dict):
    if suffix in cache:
        return cache[suffix]
    parser = None
    spec = GRAMMARS.get(suffix)
    if spec:
        try:
            from tree_sitter import Language, Parser
            language = Language(getattr(importlib.import_module(spec[0]), spec[1])())
            parser = Parser(language)
        except Exception:
            parser = None
    cache[suffix] = parser
    return parser


def _definitions(tree) -> dict[int, list]:
    """Definition-like named nodes of a tree, by start row."""
    by_row: dict[int, list] = {}
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.is_named and node.parent is not None and DEFINITION.search(node.type):
            by_row.setdefault(node.start_point[0], []).append(node)
        stack.extend(node.children)
    return by_row


def end_row(by_row: dict[int, list], row: int) -> int | None:
    """Last row of the declaration a symbol starting at `row` (0-based) belongs to."""
    direct = by_row.get(row)
    if direct:
        return max(node.end_point[0] for node in direct)
    for lead in range(1, MAX_LEAD_ROWS + 1):
        # A declaration that starts a little higher (attributes, decorators) only counts when
        # its own name is on the symbol's row; otherwise it is an enclosing class or block.
        named = [node for node in by_row.get(row - lead, ())
                 if (name := node.child_by_field_name("name")) is not None and name.start_point[0] == row]
        if named:
            return max(node.end_point[0] for node in named)
    return None


def annotate(data: dict, root: Path) -> int:
    """Set `end_line` on code nodes of graph data in place; return how many got one."""
    by_file: dict[str, list[dict]] = {}
    for node in data.get("nodes", []):
        node.pop("end_line", None)
        file = str(node.get("source_file") or "")
        match = START.fullmatch(str(node.get("source_location") or ""))
        if file and match and Path(file).suffix.lower() in GRAMMARS:
            if match.group(1) == "1" and str(node.get("label", "")) == Path(file).name:
                continue  # the file node itself
            by_file.setdefault(file, []).append(node)
    parsers: dict = {}
    count = 0
    for file, nodes in by_file.items():
        parser = _parser(Path(file).suffix.lower(), parsers)
        path = Path(file) if Path(file).is_absolute() else root / file
        if parser is None or not path.is_file():
            continue
        try:
            by_row = _definitions(parser.parse(path.read_bytes()))
        except Exception:
            continue
        for node in nodes:
            start = int(START.fullmatch(str(node["source_location"])).group(1))
            last = end_row(by_row, start - 1)
            if last is not None and last + 1 > start:
                node["end_line"] = last + 1
                count += 1
    return count


def write_atomic(path: Path, data: dict) -> None:
    fd, name = tempfile.mkstemp(prefix=path.stem + "-", suffix=".tmp", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    graph, root = Path(argv[0]), Path(argv[1])
    data = json.loads(graph.read_text(encoding="utf-8"))
    count = annotate(data, root)
    write_atomic(graph, data)
    print(f"Line ranges: {count} symbols have an end line.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
