"""
Semantic code tools for Filesystem Supertool v2, powered by the extracted solidlsp
(currently: Python via Pyright, launched on demand through ``uvx``).

One language server is started per project root the first time it's needed and
kept running for the lifetime of this process (see thread #360 decision: only
one coding instance runs at a time, so there is no multi-project concurrency
hazard to design around). Call ``shutdown_all_servers()`` on process exit.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from filesystem_supertool2.solidlsp.ls import SolidLanguageServer
from filesystem_supertool2.solidlsp.ls_config import (
    LanguageServerConfig,
    LanguageServerId,
)

_servers: dict[str, SolidLanguageServer] = {}
_servers_lock = threading.Lock()


def _get_server(project_root: str) -> SolidLanguageServer:
    project_root = os.path.abspath(project_root)
    with _servers_lock:
        srv = _servers.get(project_root)
        if srv is None:
            config = LanguageServerConfig(ls_id=LanguageServerId.PYTHON)
            srv = SolidLanguageServer.create(config, project_root)
            srv.start()
            _servers[project_root] = srv
        return srv


def shutdown_all_servers() -> None:
    """Stops every language server started by this process. Call on process exit."""
    with _servers_lock:
        for srv in _servers.values():
            srv.stop()
        _servers.clear()


# --- symbol dict conversion (UnifiedSymbolInformation / Location -> plain JSON) --------------


def _symbol_to_dict(symbol: dict[str, Any]) -> dict[str, Any]:
    location = symbol.get("location") or {}
    return {
        "name": symbol.get("name"),
        "kind": int(symbol["kind"]) if symbol.get("kind") is not None else None,
        "relative_path": location.get("relativePath"),
        "range": symbol.get("range") or location.get("range"),
        "detail": symbol.get("detail"),
        "container_name": symbol.get("containerName"),
    }


def _location_to_dict(loc: dict[str, Any]) -> dict[str, Any]:
    return {
        "relative_path": loc.get("relativePath"),
        "absolute_path": loc.get("absolutePath"),
        "range": loc.get("range"),
    }


def _find_in_tree(symbols: list[dict[str, Any]], name_path: list[str]) -> dict[str, Any] | None:
    """
    Resolves a name path (e.g. ``["ClassName", "method_name"]``) against a symbol tree,
    matching a contiguous parent-child chain that may start at any depth (to tolerate
    file/module wrapper nodes some language servers introduce at the tree root).
    """
    target, *rest = name_path
    for s in symbols:
        children = s.get("children") or []
        if s.get("name") == target:
            if not rest:
                return s
            found = _find_in_tree(children, rest)
            if found is not None:
                return found
        found = _find_in_tree(children, name_path)
        if found is not None:
            return found
    return None


def _resolve_symbol(srv: SolidLanguageServer, name_path: str, relative_path: str) -> dict[str, Any]:
    parts = [p for p in name_path.split("/") if p]
    if not parts:
        raise ValueError("name_path must not be empty")
    tree = srv.request_full_symbol_tree(within_relative_path=relative_path)
    symbol = _find_in_tree(tree, parts)
    if symbol is None:
        raise ValueError(f"symbol not found: {name_path!r} in {relative_path!r}")
    if symbol.get("range") is None:
        raise ValueError(f"symbol {name_path!r} has no range information from the language server")
    return symbol


# --- public tools -----------------------------------------------------------------------------


def find_symbol(project_root: str, name_path: str, relative_path: str | None = None) -> list[dict[str, Any]]:
    """
    Finds symbols by name. ``name_path`` may be a single name (``"foo"``) or a
    ``/``-separated path into nested scopes (``"ClassName/method_name"``).

    If ``relative_path`` is given, searches only that file's (or directory's) symbol
    tree for an exact name-path match. Otherwise, performs a workspace-wide search
    (LSP ``workspace/symbol`` -- substring/fuzzy matching, per the language server).
    """
    srv = _get_server(project_root)
    parts = [p for p in name_path.split("/") if p]
    if not parts:
        raise ValueError("name_path must not be empty")

    if relative_path is not None:
        tree = srv.request_full_symbol_tree(within_relative_path=relative_path)
        match = _find_in_tree(tree, parts)
        return [_symbol_to_dict(match)] if match is not None else []

    results = srv.request_workspace_symbol(parts[-1]) or []
    return [_symbol_to_dict(s) for s in results]


def request_definition(project_root: str, relative_path: str, line: int, column: int) -> list[dict[str, Any]]:
    """Finds the definition location(s) of the symbol at the given 0-indexed line/column."""
    srv = _get_server(project_root)
    locations = srv.request_definition(relative_path, line, column)
    return [_location_to_dict(loc) for loc in locations]


def request_references(project_root: str, relative_path: str, line: int, column: int) -> list[dict[str, Any]]:
    """Finds all reference locations of the symbol at the given 0-indexed line/column."""
    srv = _get_server(project_root)
    locations = srv.request_references(relative_path, line, column)
    return [_location_to_dict(loc) for loc in locations]


def request_document_overview(project_root: str, relative_path: str) -> list[dict[str, Any]]:
    """Lists the top-level symbols (classes, functions, etc.) defined in a file."""
    srv = _get_server(project_root)
    symbols = srv.request_document_overview(relative_path)
    return [_symbol_to_dict(s) for s in symbols]


def request_diagnostics(project_root: str, relative_path: str) -> list[dict[str, Any]]:
    """Retrieves diagnostics (errors, warnings, etc.) the language server has for a file."""
    srv = _get_server(project_root)
    diagnostics = srv.request_text_document_diagnostics(relative_path)
    return [dict(d) for d in diagnostics]


def request_rename(project_root: str, relative_path: str, line: int, column: int, new_name: str) -> dict[str, Any]:
    """
    Renames the symbol at the given 0-indexed line/column to ``new_name`` across the
    workspace, applying the edit immediately (there is no separate preview/apply step).
    """
    from filesystem_supertool2.solidlsp.ls_utils import FileUtils

    srv = _get_server(project_root)
    edit = srv.request_rename_symbol_edit(relative_path, line, column, new_name)
    if edit is None:
        raise ValueError("the language server did not return a rename edit (rename may not be supported here)")

    changed_files: list[str] = []
    for uri, edits in (edit.get("changes") or {}).items():
        abs_path = FileUtils.uri_to_path(uri)
        rel = os.path.relpath(abs_path, os.path.abspath(project_root))
        srv.apply_text_edits_to_file(rel, edits)
        changed_files.append(rel)

    return {"new_name": new_name, "changed_files": changed_files}


def replace_symbol_body(project_root: str, name_path: str, relative_path: str, new_body: str) -> dict[str, Any]:
    """Replaces the full body (definition through end) of the named symbol with ``new_body``."""
    srv = _get_server(project_root)
    symbol = _resolve_symbol(srv, name_path, relative_path)
    rng = symbol["range"]
    srv.apply_text_edits_to_file(relative_path, [{"range": rng, "newText": new_body}])
    return {"relative_path": relative_path, "name_path": name_path, "range": rng}


def insert_before_symbol(project_root: str, name_path: str, relative_path: str, text: str) -> dict[str, Any]:
    """Inserts ``text`` immediately before the named symbol (at the start of its start line)."""
    srv = _get_server(project_root)
    symbol = _resolve_symbol(srv, name_path, relative_path)
    start = symbol["range"]["start"]
    srv.insert_text_at_position(relative_path, start["line"], 0, text)
    return {"relative_path": relative_path, "name_path": name_path, "inserted_at_line": start["line"]}


def insert_after_symbol(project_root: str, name_path: str, relative_path: str, text: str) -> dict[str, Any]:
    """Inserts ``text`` immediately after the named symbol (at the start of the line following its end)."""
    srv = _get_server(project_root)
    symbol = _resolve_symbol(srv, name_path, relative_path)
    end = symbol["range"]["end"]
    # end.character > 0 means the range ends mid-line, so "after" starts on the next line;
    # end.character == 0 already denotes the start of the following line (LSP convention).
    insert_line = end["line"] + 1 if end["character"] > 0 else end["line"]
    srv.insert_text_at_position(relative_path, insert_line, 0, text)
    return {"relative_path": relative_path, "name_path": name_path, "inserted_at_line": insert_line}
