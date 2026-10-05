"""neo4j-admin import files (SPEC 5.5 header syntax): one CSV per node label, one per relationship type.

Every string is quoted (quotes doubled), arrays use ";" between elements, a missing value is an empty
unquoted field (= no property). Headers come from codegraph_common.schema, so types are exact:
`line:int`, `isTest:boolean`, `attributes:string[]`. Rows never contain raw newlines (the merge step
stores them as \\n), so the importer can parse every file in parallel.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, TextIO

from codegraph_common.log import log
from codegraph_common.schema import BOOL, INT, INTS, STRINGS, node_props, rel_props


def _quote(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'


def _cell(value: Any, kind: str) -> str:
    if value is None:
        return ""
    if kind == BOOL:
        return "true" if value else "false"
    if kind == INT:
        return str(int(value))
    if kind == INTS:
        return ";".join(str(int(v)) for v in value)
    if kind == STRINGS:
        return _quote(";".join(str(v) for v in value)) if value else ""
    return _quote(str(value))


class CsvWriter:
    """Streams relationship rows (any order) and writes node files at the end."""

    def __init__(self, folder: Path):
        self.folder = folder
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        self.node_files: list[Path] = []
        self.rel_files: list[Path] = []
        self._rel_handles: dict[str, tuple[TextIO, list[tuple[str, str]]]] = {}
        self.dropped_properties: dict[str, int] = {}

    # ---- relationships -----------------------------------------------------------------------

    def write_edge(self, edge: dict[str, Any]) -> None:
        rel_type = edge["type"]
        handle = self._rel_handles.get(rel_type)
        if handle is None:
            handle = self._open_rel_file(rel_type)
        stream, columns = handle
        cells = [_quote(edge["start"]), _quote(edge["end"]), rel_type]
        cells += [_cell(edge.get(name), kind) for name, kind in columns]
        stream.write(",".join(cells))
        stream.write("\n")
        for key in edge.keys() - self._known_rel_keys(rel_type):
            self.dropped_properties[f"{rel_type}.{key}"] = self.dropped_properties.get(f"{rel_type}.{key}", 0) + 1

    def _open_rel_file(self, rel_type: str) -> tuple[TextIO, list[tuple[str, str]]]:
        path = self.folder / f"rels_{rel_type}.csv"
        columns = list(rel_props(rel_type).items())
        stream = open(path, "w", encoding="utf-8", newline="")
        header = [":START_ID", ":END_ID", ":TYPE"] + [f"{name}:{kind}" for name, kind in columns]
        stream.write(",".join(header) + "\n")
        self._rel_handles[rel_type] = (stream, columns)
        self.rel_files.append(path)
        return stream, columns

    @staticmethod
    def _known_rel_keys(rel_type: str) -> set[str]:
        return {"type", "start", "end", *rel_props(rel_type)}

    # ---- nodes -------------------------------------------------------------------------------

    def write_nodes(self, label: str, nodes: list[dict[str, Any]]) -> None:
        schema = node_props(label)
        # Properties outside the schema are kept as strings (and reported) rather than silently dropped.
        extra = sorted({key for node in nodes for key in node} - set(schema))
        if extra:
            log("loader", f"warning: {label} has properties outside the schema, imported as strings: {extra}")
        columns = [(name, kind) for name, kind in schema.items() if name != "id"] + [(name, "string") for name in extra]
        path = self.folder / f"nodes_{label}.csv"
        with open(path, "w", encoding="utf-8", newline="") as stream:
            header = ["id:ID", ":LABEL"] + [f"{name}:{kind}" for name, kind in columns]
            stream.write(",".join(header) + "\n")
            labels = f"{label};Node"
            for node in nodes:
                row = [_quote(node["id"]), labels] + [_cell(node.get(name), kind) for name, kind in columns]
                stream.write(",".join(row))
                stream.write("\n")
        self.node_files.append(path)

    def close(self) -> None:
        for stream, _ in self._rel_handles.values():
            stream.close()
        if self.dropped_properties:
            log("loader", f"warning: relationship properties outside the schema were dropped: {self.dropped_properties}")
