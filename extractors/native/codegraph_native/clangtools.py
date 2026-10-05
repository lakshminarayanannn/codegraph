"""libclang setup and the few C API calls the Python bindings do not wrap."""
from __future__ import annotations

import ctypes
import re
import shutil
import subprocess
from functools import lru_cache

import clang.cindex as ci

PARSE_KEEP_GOING = 0x200  # CXTranslationUnit_KeepGoing: keep parsing after fatal errors (missing headers)


@lru_cache(maxsize=1)
def _lib() -> ctypes.CDLL:
    lib = ci.conf.lib
    lib.clang_getOverriddenCursors.argtypes = [ci.Cursor, ctypes.POINTER(ctypes.POINTER(ci.Cursor)), ctypes.POINTER(ctypes.c_uint)]
    lib.clang_getOverriddenCursors.restype = None
    lib.clang_disposeOverriddenCursors.argtypes = [ctypes.POINTER(ci.Cursor)]
    lib.clang_disposeOverriddenCursors.restype = None
    return lib


def overridden_usrs(cursor: ci.Cursor) -> list[str]:
    """USRs of the methods this virtual method overrides (clang_getOverriddenCursors - exact, not by signature)."""
    lib = _lib()
    cursors = ctypes.POINTER(ci.Cursor)()
    count = ctypes.c_uint()
    lib.clang_getOverriddenCursors(cursor, ctypes.byref(cursors), ctypes.byref(count))
    try:
        # Read the USRs before the array is freed.
        return [cursors[i].get_usr() for i in range(count.value)]
    finally:
        if count.value:
            lib.clang_disposeOverriddenCursors(cursors)


class _RawCXString(ctypes.Structure):
    # Same layout as CXString, without cindex._CXString's __del__ (which would free it a second time).
    _fields_ = [("data", ctypes.c_char_p), ("flags", ctypes.c_uint)]


class _CXStringSet(ctypes.Structure):
    _fields_ = [("strings", ctypes.POINTER(_RawCXString)), ("count", ctypes.c_uint)]


def all_manglings(cursor: ci.Cursor) -> list[str]:
    """Every symbol name a constructor/destructor gets (complete, base, deleting...): dumpbin lists those,
    while Cursor.mangled_name gives only one of them."""
    lib = ci.conf.lib
    lib.clang_Cursor_getCXXManglings.argtypes = [ci.Cursor]
    lib.clang_Cursor_getCXXManglings.restype = ctypes.POINTER(_CXStringSet)
    lib.clang_disposeStringSet.argtypes = [ctypes.POINTER(_CXStringSet)]
    lib.clang_disposeStringSet.restype = None
    result = lib.clang_Cursor_getCXXManglings(cursor)
    if not result:
        return []
    try:
        # A CXString's first field is the C string itself for strings libclang creates.
        strings = result.contents.strings
        return [strings[i].data.decode("utf-8", "replace") for i in range(result.contents.count) if strings[i].data]
    finally:
        lib.clang_disposeStringSet(result)


def clang_version() -> str:
    lib = ci.conf.lib
    lib.clang_getClangVersion.restype = ci._CXString
    lib.clang_getClangVersion.errcheck = ci._CXString.from_result
    return lib.clang_getClangVersion()


def resource_dir(configured: str | None, libclang_version: str = "") -> str | None:
    """Clang's builtin headers (stddef.h, intrin.h...): the pip libclang does not ship them.

    Uses native.resource_dir from the config, else asks a clang on PATH. Without them, code that includes
    system headers may report errors (see diagnostics.json) - install LLVM and set native.resource_dir.
    """
    if configured:
        return configured
    for compiler in ("clang", "clang-cl"):
        path = shutil.which(compiler)
        if path:
            flag = ["-print-resource-dir"] if compiler == "clang" else ["/clang:-print-resource-dir"]
            try:
                result = subprocess.run([path, *flag], capture_output=True, text=True, timeout=20)
            except (OSError, subprocess.TimeoutExpired):
                continue
            found = result.stdout.strip()
            # Builtin headers must come from the same major version as libclang (e.g. .../clang/18).
            wanted = re.search(r"version (\d+)", libclang_version)
            if result.returncode == 0 and found and (wanted is None or found.rstrip("/\\").endswith(wanted.group(1))):
                return found
    return None
