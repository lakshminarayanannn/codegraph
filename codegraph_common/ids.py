"""Stable node IDs (SPEC 4.1) - the Python twin of extractors/CodeGraph.Shared/Ids.cs.

Every rule here must produce byte-identical strings to the C# side, because nodes from different
extractors join only on these IDs.
"""
from __future__ import annotations

import hashlib
import posixpath


def normalize_path(path: str) -> str:
    return path.replace("\\", "/")


def file_id(repo_relative_path: str) -> str:
    return "file:" + normalize_path(repo_relative_path).lower()


def assembly_id(name: str) -> str:
    return "asm:" + name


def normalize_dll(dll: str) -> str:
    """Same module named three ways ("X", "X.dll", "..\\bin\\X.DLL") -> "x.dll"."""
    name = posixpath.basename(normalize_path(dll)).lower()
    return name if name.endswith((".dll", ".exe")) else name + ".dll"


def export_id(dll: str, entry_point: str) -> str:
    return f"exp:{normalize_dll(dll)}!{entry_point}"


def message_id(template: str, emitter_id: str) -> str:
    return "msg:" + hashlib.sha1(f"{template}|{emitter_id}".encode()).hexdigest()


def config_id(key: str) -> str:
    return "cfg:" + key


def native_id(usr: str) -> str:
    return "cpp:" + usr


def unresolved_native_id(name: str) -> str:
    return "cpp:unresolved:" + name


def unresolved_native_type_id(name: str) -> str:
    return "cpp:unresolved-type:" + name
