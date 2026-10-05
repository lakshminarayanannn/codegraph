"""Loads config/codegraph.yaml for the Python tools.

Relative paths resolve against the folder that holds the config file (same rule as the C# extractors).
Secrets never live in the YAML: passwords come from the environment variables it names, optionally
filled from a git-ignored .env file next to the config or in the codegraph checkout.
"""
from __future__ import annotations

import fnmatch
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# The codegraph checkout (this file lives in <checkout>/codegraph_common/).
CHECKOUT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Neo4jSettings:
    uri: str
    database: str
    user_writer: str
    password_writer: str | None
    user_reader: str
    password_reader: str | None
    home: str
    import_method: str

    def reader_auth(self) -> tuple[str, str]:
        """Read-only account if configured, else the writer (queries still run in READ transactions)."""
        if self.password_reader:
            return self.user_reader, self.password_reader
        return self.user_writer, self.password_writer or ""

    def writer_auth(self) -> tuple[str, str]:
        return self.user_writer, self.password_writer or ""

    def tool(self, name: str) -> str:
        """Path of a Neo4j command (neo4j, neo4j-admin): from neo4j.home, else from PATH."""
        suffix = ".bat" if os.name == "nt" else ""
        if self.home:
            return str(Path(self.home) / "bin" / f"{name}{suffix}")
        found = shutil.which(name)
        if not found:
            raise FileNotFoundError(f"{name} not found: set neo4j.home in the config or put Neo4j's bin on PATH")
        return found


class Config:
    """Typed accessors over the raw YAML dictionary; unknown keys are kept in `raw`."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path).resolve()
        self.dir = self.path.parent
        self.raw: dict[str, Any] = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        load_dotenv([self.dir / ".env", self.dir.parent / ".env", CHECKOUT / ".env", Path.cwd() / ".env"])

    def resolve(self, value: str | os.PathLike[str]) -> Path:
        candidate = Path(value)
        return candidate if candidate.is_absolute() else (self.dir / candidate).resolve()

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    @property
    def repo_root(self) -> Path:
        return self.resolve(self.raw.get("repo_root", "."))

    @property
    def output_dir(self) -> Path:
        return self.resolve(self.raw.get("output_dir", "out"))

    @property
    def import_dir(self) -> Path:
        return self.output_dir / "import"

    def extractor_dirs(self) -> dict[str, Path]:
        """Where each extractor writes its per-owner JSONL files."""
        return {origin: self.output_dir / origin for origin in ("csharp", "bridge", "native")}

    @property
    def layers(self) -> list[dict[str, Any]]:
        return list(self.raw.get("layers") or [])

    def layer_of(self, assembly: str) -> tuple[str, int] | tuple[None, None]:
        """First configured layer whose patterns match the assembly name (case-insensitive glob)."""
        for index, layer in enumerate(self.layers):
            for pattern in layer.get("assemblies", []):
                if fnmatch.fnmatchcase(assembly.lower(), str(pattern).lower()):
                    return layer["name"], index
        return None, None

    @property
    def git_history(self) -> dict[str, int]:
        settings = {"window_days": 730, "co_change_min": 5, "co_change_max_files": 30}
        settings.update(self.raw.get("git_history") or {})
        return settings

    @property
    def log_prefix_regex(self) -> str | None:
        return self.raw.get("log_prefix_regex")

    @property
    def limits(self) -> dict[str, int]:
        settings = {"max_depth": 8, "max_results": 200, "query_timeout_s": 15}
        settings.update(self.raw.get("limits") or {})
        return settings

    @property
    def native(self) -> dict[str, Any]:
        settings: dict[str, Any] = {
            "compdb": str(self.output_dir / "compile_commands.json"),
            "target": "x86_64-pc-windows-msvc",
            "extra_args": ["-fms-compatibility", "-fms-extensions", "-fdelayed-template-parsing", "-Wno-everything"],
            "exports_from": "dumpbin",
            "modules": [],
            "max_workers": 0,
        }
        settings.update(self.raw.get("native") or {})
        return settings

    @property
    def native_log_functions(self) -> list[dict[str, Any]]:
        return list(self.raw.get("native_log_functions") or [])

    @property
    def native_dlls(self) -> list[Path]:
        return [self.resolve(p) for p in self.raw.get("native_dlls") or []]

    @property
    def cppcli_assemblies(self) -> list[dict[str, Any]]:
        """Normalized like the C# side: {"path", "name", "source_dir"}."""
        result = []
        for item in self.raw.get("cppcli_assemblies") or []:
            entry = {"path": item} if isinstance(item, str) else dict(item)
            path = self.resolve(entry["path"])
            result.append({
                "path": path,
                "name": entry.get("name") or path.stem,
                "source_dir": self.resolve(entry["source_dir"]) if entry.get("source_dir") else None,
            })
        return result

    @property
    def neo4j(self) -> Neo4jSettings:
        raw = self.raw.get("neo4j") or {}
        return Neo4jSettings(
            uri=os.environ.get("CODEGRAPH_NEO4J_URI", raw.get("uri", "bolt://localhost:7687")),
            database=raw.get("database", "neo4j"),
            user_writer=raw.get("user_writer", "neo4j"),
            password_writer=os.environ.get(raw.get("password_writer_env", "CODEGRAPH_NEO4J_PASSWORD")),
            user_reader=raw.get("user_reader", "codegraph_ro"),
            password_reader=os.environ.get(raw.get("password_reader_env", "CODEGRAPH_NEO4J_RO_PASSWORD")),
            home=raw.get("home") or "",
            import_method=raw.get("import_method", "admin"),
        )


def load_dotenv(candidates: list[Path]) -> None:
    """Reads KEY=VALUE lines from the first existing .env; never overrides variables already set."""
    for candidate in candidates:
        if candidate.is_file():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
            return
