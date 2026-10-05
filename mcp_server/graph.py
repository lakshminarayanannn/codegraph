"""Read-only graph access for the MCP server (SPEC 10).

* Every query runs in a READ transaction with a timeout: Neo4j refuses writes in READ mode, which is
  the safety net Community Edition offers in place of role-based access control.
* Every tool result carries the index commit and whether it is stale against the repository HEAD.
"""
from __future__ import annotations

import subprocess
import time
from typing import Any

from neo4j import GraphDatabase, unit_of_work

from codegraph_common.config import Config

# Properties returned for a node in tool output: enough to locate and identify it, nothing bulky.
NODE_FIELDS = """{id: n.id, labels: labels(n), name: n.name, fqn: n.fqn, signature: n.signature, kind: n.kind,
 lang: n.lang, assembly: n.assembly, file: n.file, line: n.lineStart, isTest: n.isTest,
 isEntryPoint: n.isEntryPoint, entryKind: n.entryKind, isPublicApi: n.isPublicApi, accessibility: n.accessibility,
 typeId: n.typeId, resolved: n.resolved, locationConfidence: n.locationConfidence}"""


def node_fields(variable: str) -> str:
    return NODE_FIELDS.replace("n.", f"{variable}.").replace("labels(n)", f"labels({variable})")


class ReadOnlyGraph:
    def __init__(self, config: Config):
        self.config = config
        settings = config.neo4j
        self.database = settings.database
        self.timeout = float(config.limits["query_timeout_s"])
        self.driver = GraphDatabase.driver(settings.uri, auth=settings.reader_auth(), notifications_min_severity="OFF")
        self._index_cache: tuple[float, dict[str, Any]] | None = None

    def close(self) -> None:
        self.driver.close()

    def query(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        @unit_of_work(timeout=self.timeout)
        def work(tx: Any) -> list[dict[str, Any]]:
            return [record.data() for record in tx.run(cypher, params or {})]

        with self.driver.session(database=self.database, default_access_mode="READ") as session:
            return session.execute_read(work)

    def index_info(self) -> dict[str, Any]:
        """index_commit, stale and indexed_at, cached for 30 seconds (SPEC 10: every result ends with them)."""
        now = time.time()
        if self._index_cache and now - self._index_cache[0] < 30:
            return self._index_cache[1]
        rows = self.query("MATCH (r:IndexRun) RETURN r.commit AS commit, r.finishedAt AS finishedAt, r.mode AS mode "
                          "ORDER BY r.startedAt DESC LIMIT 1")
        info: dict[str, Any] = {"index_commit": None, "stale": None, "indexed_at": None}
        if rows:
            info["index_commit"] = rows[0]["commit"]
            info["indexed_at"] = rows[0]["finishedAt"]
            head = _git_head(self.config)
            if head is not None and rows[0]["commit"] not in (None, "nogit"):
                info["stale"] = head != rows[0]["commit"]
        self._index_cache = (now, info)
        return info


def _git_head(config: Config) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(config.repo_root), "rev-parse", "HEAD"],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def compact(node: dict[str, Any] | None) -> dict[str, Any] | None:
    """Tool-facing view of a node: label, ids, `loc` as file:line, and only the flags that are set."""
    if node is None:
        return None
    labels = [label for label in node.get("labels") or [] if label != "Node"]
    result: dict[str, Any] = {"id": node["id"], "label": labels[0] if labels else None}
    for key in ("name", "fqn", "signature", "kind", "lang", "assembly", "entryKind", "accessibility"):
        if node.get(key) not in (None, ""):
            result[key] = node[key]
    if node.get("file"):
        line = node.get("line")
        result["loc"] = f"{node['file']}:{line}" if line and line > 0 else node["file"]
    for flag in ("isTest", "isEntryPoint", "isPublicApi"):
        if node.get(flag):
            result[flag] = True
    if node.get("resolved") is False:
        result["resolved"] = False
    if node.get("locationConfidence"):
        result["locationConfidence"] = node["locationConfidence"]
    return result


def brief(node: dict[str, Any]) -> dict[str, Any]:
    """List view (what traversal results repeat many times, so it is kept small): the id - readable for managed
    symbols - plus fqn only where the id is not readable (native USRs, exports), location and key flags."""
    view: dict[str, Any] = {"id": node["id"]}
    if not node["id"].startswith("dn:") and node.get("fqn"):
        view["fqn"] = node["fqn"]
    if node.get("file"):
        line = node.get("line")
        view["loc"] = f"{node['file']}:{line}" if line and line > 0 else node["file"]
    for flag in ("isTest", "isEntryPoint"):
        if node.get(flag):
            view[flag] = True
    if node.get("isEntryPoint") and node.get("entryKind"):
        view["entryKind"] = node["entryKind"]
    if node.get("resolved") is False:
        view["resolved"] = False
    return view


def label(node: dict[str, Any]) -> str:
    """Readable name for paths: the id for managed symbols (it includes the parameter types), else the fqn."""
    node_id = node.get("id", "")
    return node_id if node_id.startswith("dn:") or not node.get("fqn") else node["fqn"]


def clamp(value: int | None, default: int, upper: int) -> int:
    """Server-side bound on depth and limit arguments (SPEC 10: clamp max_depth and limit)."""
    try:
        number = int(value) if value is not None else default
    except (TypeError, ValueError):
        number = default
    return max(1, min(number, upper))
