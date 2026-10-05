"""Creates the account the MCP server connects with (neo4j.user_reader).

Enterprise Edition: the account also gets the built-in `reader` role, so writes are impossible.
Community Edition has no roles (every account can write), so the server's protection is that all its
queries are fixed and run in READ transactions, which Neo4j refuses to write in (SPEC 10).
"""
from __future__ import annotations

from codegraph_common.config import Config
from codegraph_common.log import log

from .neo import Graph


def ensure_reader(config: Config) -> None:
    settings = config.neo4j
    if not settings.password_reader:
        log("loader", "no reader password set (neo4j.password_reader_env): the MCP server will use the writer "
                      "account, still in READ transactions")
        return
    graph = Graph(settings)
    try:
        def system(query: str, **params: object) -> list[dict]:
            records, _, _ = graph.driver.execute_query(query, params, database_="system")
            return [r.data() for r in records]

        exists = system("SHOW USERS YIELD user WHERE user = $name RETURN user", name=settings.user_reader)
        if exists:
            try:
                system("ALTER USER $name SET PASSWORD $password CHANGE NOT REQUIRED",
                       name=settings.user_reader, password=settings.password_reader)
                log("loader", f"updated password of {settings.user_reader}")
            except Exception as error:  # same password as before: nothing to change
                if "cannot be the same" not in str(error):
                    raise
        else:
            system("CREATE USER $name SET PASSWORD $password CHANGE NOT REQUIRED",
                   name=settings.user_reader, password=settings.password_reader)
            log("loader", f"created user {settings.user_reader}")
        try:
            system("GRANT ROLE reader TO $name", name=settings.user_reader)
            log("loader", f"granted role reader to {settings.user_reader}")
        except Exception:  # Community Edition: no role management
            log("loader", "role management unavailable (Community Edition): relying on READ transactions")
    finally:
        graph.close()
