"""Compilation database (SPEC 6.1, 6.2): load compile_commands.json, split off C++/CLI units, and turn each
MSVC command line into arguments libclang can parse with (clang-cl driver mode)."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

_CLR = re.compile(r"^[/-]clr(:.*)?$", re.IGNORECASE)

# Options that only name outputs or precompiled headers: they do not change what the code means.
# MSVC options are case-sensitive: /FI (forced include) must stay, /Fo (object file) must go.
_DROP_PREFIXES = ("/Fo", "/Fd", "/Fp", "/Fa", "/FR", "/Fr", "/Fe", "/Fm", "/Yu", "/Yc", "/Yl", "/MP", "/errorReport",
                  "-Fo", "-Fd", "-Fp", "-Fa", "-FR", "-Fr", "-Fe", "-Fm", "-Yu", "-Yc", "-Yl", "-MP", "-errorReport")
_DROP_EXACT = {"/c", "-c", "/nologo", "-nologo", "/FS", "-FS", "/Gm", "/Gm-", "/JMC", "/sdl", "/analyze"}


@dataclass
class Unit:
    file: str            # absolute path of the source file
    directory: str
    args: list[str]      # compiler arguments without the compiler, outputs, PCH options or the source itself
    is_clr: bool


def load(path: str | os.PathLike[str]) -> list[Unit]:
    entries = json.loads(Path(path).read_text(encoding="utf-8"))
    units = []
    for entry in entries:
        directory = entry.get("directory", ".")
        source = os.path.normpath(os.path.join(directory, entry["file"]))
        argv = entry.get("arguments") or split_command(entry["command"])
        units.append(Unit(file=source, directory=directory, args=clean(argv[1:], source, directory),
                          is_clr=any(_CLR.match(a) for a in argv)))
    return units


def clean(argv: list[str], source: str, directory: str) -> list[str]:
    kept = []
    for arg in argv:
        if arg in _DROP_EXACT or arg.startswith(_DROP_PREFIXES):
            continue
        if os.path.normpath(os.path.join(directory, arg)) == source:
            continue  # the source file itself (on Unix it starts with "/" like an option): libclang gets it separately
        kept.append(arg)
    return kept


def split_command(command: str) -> list[str]:
    """Windows command-line splitting (CommandLineToArgvW rules), which MSVC command lines follow:
    2n backslashes + quote -> n backslashes and a quote toggle; 2n+1 backslashes + quote -> n backslashes and
    a literal quote; backslashes not followed by a quote are literal."""
    args: list[str] = []
    current: list[str] = []
    in_quotes = started = False
    i = 0
    while i < len(command):
        char = command[i]
        if char == "\\":
            end = i
            while end < len(command) and command[end] == "\\":
                end += 1
            count = end - i
            if end < len(command) and command[end] == '"':
                current.append("\\" * (count // 2))
                if count % 2:
                    current.append('"')
                    end += 1
            else:
                current.append("\\" * count)
            i, started = end, True
        elif char == '"':
            in_quotes, started = not in_quotes, True
            i += 1
        elif char in " \t" and not in_quotes:
            if started:
                args.append("".join(current))
                current, started = [], False
            i += 1
        else:
            current.append(char)
            started = True
            i += 1
    if started:
        args.append("".join(current))
    return args
