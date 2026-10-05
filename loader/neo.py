"""Neo4j access for the loader: connection, batched writes, and the online (Bolt) upserts used by the
incremental loader and by `--method bolt` full loads."""
from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

from neo4j import Driver, GraphDatabase

from codegraph_common.config import Neo4jSettings
from codegraph_common.log import log
from codegraph_common.schema import SCHEMA_STATEMENTS

BATCH = 5000  # rows per transaction: large enough to amortize round trips, small enough for the heap


class Graph:
    def __init__(self, settings: Neo4jSettings, writer: bool = True):
        self.settings = settings
        auth = settings.writer_auth() if writer else settings.reader_auth()
        self.driver: Driver = GraphDatabase.driver(settings.uri, auth=auth, notifications_min_severity="OFF")

    def close(self) -> None:
        self.driver.close()

    def run(self, query: str, **params: Any) -> list[dict[str, Any]]:
        records, _, _ = self.driver.execute_query(query, params, database_=self.settings.database)
        return [record.data() for record in records]

    def autocommit(self, query: str, **params: Any) -> None:
        """Auto-commit transaction: required by CALL { ... } IN TRANSACTIONS (batched deletes)."""
        with self.driver.session(database=self.settings.database) as session:
            session.run(query, params).consume()

    def write_batches(self, query: str, rows: Iterable[dict[str, Any]], batch: int = BATCH) -> int:
        """Runs `UNWIND $rows ...` queries in chunks; returns the number of rows sent."""
        total = 0
        chunk: list[dict[str, Any]] = []
        for row in rows:
            chunk.append(row)
            if len(chunk) >= batch:
                self.run(query, rows=chunk)
                total += len(chunk)
                chunk = []
        if chunk:
            self.run(query, rows=chunk)
            total += len(chunk)
        return total

    def wait_until_available(self, timeout_s: int = 180) -> None:
        deadline = time.time() + timeout_s
        while True:
            try:
                self.driver.verify_connectivity()
                self.run("RETURN 1")
                return
            except Exception as error:  # the server is still starting: retry until the deadline
                if time.time() > deadline:
                    raise TimeoutError(f"Neo4j at {self.settings.uri} not available after {timeout_s}s: {error}") from error
                time.sleep(2)

    def apply_schema(self) -> None:
        """Constraints and indexes (SPEC 4.4), then wait until they are online."""
        for statement in SCHEMA_STATEMENTS:
            self.run(statement)
        self.run("CALL db.awaitIndexes(600)")


# ---- online upserts (Bolt) -------------------------------------------------------------------

def upsert_nodes(graph: Graph, label: str, nodes: list[dict[str, Any]], only_create: bool = False) -> None:
    """MERGE on the universal :Node(id) index. `only_create` leaves existing nodes untouched (stubs)."""
    if only_create:
        query = (f"UNWIND $rows AS row MERGE (n:Node {{id: row.id}}) "
                 f"ON CREATE SET n = row, n:{label}")
    else:
        query = (f"UNWIND $rows AS row MERGE (n:Node {{id: row.id}}) "
                 f"SET n = row, n:{label} REMOVE n:External")
    graph.write_batches(query, (_without_nulls(n) for n in nodes))


def create_edges(graph: Graph, rel_type: str, edges: Iterable[dict[str, Any]]) -> int:
    query = (f"UNWIND $rows AS row MATCH (a:Node {{id: row.start}}) MATCH (b:Node {{id: row.end}}) "
             f"CREATE (a)-[r:{rel_type}]->(b) SET r = row.props")
    rows = ({"start": e["start"], "end": e["end"],
             "props": _without_nulls({k: v for k, v in e.items() if k not in ("type", "start", "end")})} for e in edges)
    return graph.write_batches(query, rows)


def delete_all(graph: Graph) -> None:
    """Empties the database in batches (a single huge transaction would exhaust the heap)."""
    graph.autocommit("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS")
    log("loader", "database emptied")


def _without_nulls(values: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v is not None and v != []}
