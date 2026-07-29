"""
Filesystem operations for Filesystem Supertool v2.

Reimplements, tool-for-tool, the feature set of the current (Node/TypeScript)
Filesystem Supertool. Functions here are plain Python -- ``server.py`` wires
them up as MCP tools. Every function raises ``ValueError`` on bad input or
``OSError``/``FileNotFoundError``/etc. on filesystem failure; the MCP layer
is responsible for turning those into tool-call errors.
"""

from __future__ import annotations

import base64
import difflib
import hashlib
import os
import re
import shutil
import socket
import stat as stat_module
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

MAX_READ_FILE_BYTES = 10 * 1024 * 1024  # 10MB, matches the current tool's stated limit

DEFAULT_FIND_EXCLUDE = "node_modules/**,.git/**"
DEFAULT_GREP_EXCLUDE = "node_modules/**,.git/**,*.pyc,*.min.js"
DEFAULT_TREE_EXCLUDE = "node_modules,.git,__pycache__,venv,.venv"


# --- path validation ----------------------------------------------------------------
#
# Mirrors the current (Node/TypeScript) Filesystem Supertool's lib/paths.js
# validatePath() exactly: absolute-path syntax check, resolve to remove . / ..,
# confirm the resolved path sits inside one of the configured allowed roots (exact
# match or subdirectory), and -- separately -- if the path exists, resolve symlinks
# and re-check the *target* against the allowed roots too (symlink-escape defence);
# if it doesn't exist yet, the parent directory is checked instead. Order matters:
# the original checks must_exist before the not-exists/parent-directory branch, so
# a must_exist=True call on a missing path raises "does not exist" rather than
# silently validating the parent.

_allowed_directories: list[str] = []


def set_allowed_directories(dirs: list[str]) -> None:
    """Configures the roots all path-accepting tools are restricted to. Call once at startup."""
    global _allowed_directories
    _allowed_directories = [os.path.normpath(os.path.abspath(d)) for d in dirs]


def get_allowed_directories() -> list[str]:
    return list(_allowed_directories)


def _is_within_allowed(target: str) -> bool:
    normalized = os.path.normpath(target)
    for allowed in _allowed_directories:
        if normalized == allowed or normalized.startswith(allowed + os.sep):
            return True
    return False


def _check_absolute_syntax(path: str) -> None:
    if not path:
        raise ValueError("path must not be empty")
    if path.startswith("~"):
        raise ValueError(f"path must not use ~ expansion, use a full path: {path!r}")
    is_windows_abs = len(path) >= 3 and path[1] == ":" and path[2] in "\\/"
    if not (path.startswith("/") or is_windows_abs):
        raise ValueError(f"path must be absolute (start with / or a drive letter): {path!r}")


def validate_path(path: str, *, must_exist: bool = False, follow_symlinks: bool = True) -> str:
    """
    Validates a path against the allowed-directories boundary and returns its
    resolved (normalized, symlink-followed-if-applicable) absolute form.

    :raises ValueError: empty/relative/~-path, or resolves outside the allowed roots
    :raises FileNotFoundError: must_exist=True and the path doesn't exist, or (for a
        not-yet-existing path) no ancestor directory exists at all
    """
    _check_absolute_syntax(path)
    if not _allowed_directories:
        raise ValueError("no allowed directories configured; the server must be started with at least one allowed root")

    resolved = os.path.normpath(os.path.abspath(path))
    if not _is_within_allowed(resolved):
        raise ValueError(f"Access denied: path outside allowed directories\nRequested: {resolved}\nAllowed: {', '.join(_allowed_directories)}")

    exists = os.path.exists(resolved)
    if must_exist and not exists:
        raise FileNotFoundError(f"Path does not exist: {resolved}")

    if not exists:
        # Walk up to the nearest *existing* ancestor rather than requiring the
        # immediate parent to already exist: the original tool only checks the
        # immediate parent here, which silently breaks its own advertised
        # "mkdir -p"-style recursive creation for anything nested more than one
        # missing level deep (verified by reading its create_directory tool,
        # which calls mkdir({recursive:true}) right after a check that can't
        # accommodate recursion). Same security property either way -- nothing
        # outside the allowed roots is ever touched -- just without that gap.
        ancestor = os.path.dirname(resolved)
        while ancestor and not os.path.exists(ancestor):
            parent_of_ancestor = os.path.dirname(ancestor)
            if parent_of_ancestor == ancestor:  # reached filesystem root without finding one
                break
            ancestor = parent_of_ancestor
        if not os.path.isdir(ancestor):
            raise FileNotFoundError(f"No existing ancestor directory found for: {resolved}")
        ancestor_real = os.path.realpath(ancestor)
        if not _is_within_allowed(ancestor_real):
            raise ValueError(
                f"Access denied: nearest existing ancestor directory resolves outside allowed directories\n"
                f"Ancestor: {ancestor_real}\nAllowed: {', '.join(_allowed_directories)}"
            )
        return resolved

    if follow_symlinks:
        real = os.path.realpath(resolved)
        if not _is_within_allowed(real):
            raise ValueError(
                f"Access denied: symlink target outside allowed directories\n"
                f"Symlink: {resolved}\nTarget: {real}\nAllowed: {', '.join(_allowed_directories)}"
            )
        return real

    return resolved


# --- glob matching (supports **, *, ?, [abc], {a,b,c}) -------------------------------


def _expand_braces(pattern: str) -> list[str]:
    """Expands one level of shell-style {a,b,c} alternation in a glob pattern."""
    start = pattern.find("{")
    if start == -1:
        return [pattern]
    depth = 0
    end = -1
    for i in range(start, len(pattern)):
        if pattern[i] == "{":
            depth += 1
        elif pattern[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return [pattern]
    prefix = pattern[:start]
    suffix = pattern[end + 1 :]
    options = pattern[start + 1 : end].split(",")
    expanded = [prefix + opt + suffix for opt in options]
    result: list[str] = []
    for e in expanded:
        result.extend(_expand_braces(e))
    return result


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """
    Translates one glob pattern (no braces -- expand those first) to a regex,
    matched against a POSIX-style relative path (forward slashes).

    - ``**`` matches any characters including ``/``
    - ``*``  matches any characters except ``/``
    - ``?``  matches exactly one character except ``/``
    - ``[abc]`` matches any single character in the set (passed through to regex)
    """
    i = 0
    out = []
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                if i + 2 < n and pattern[i + 2] == "/":
                    # "**/" also matches zero directories, so "**/*.py" matches at the root too
                    out.append("(?:.*/)?")
                    i += 3
                else:
                    out.append(".*")
                    i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = pattern.find("]", i + 1)
            if j == -1:
                out.append(re.escape(c))
                i += 1
            else:
                out.append(pattern[i : j + 1])
                i = j + 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _split_top_level_commas(pattern: str) -> list[str]:
    """Splits on commas that separate whole patterns, ignoring commas nested inside {a,b,c} groups."""
    parts = []
    depth = 0
    current = []
    for c in pattern:
        if c == "{":
            depth += 1
            current.append(c)
        elif c == "}":
            depth = max(0, depth - 1)
            current.append(c)
        elif c == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(c)
    parts.append("".join(current))
    return parts


def _compile_glob_patterns(pattern: str) -> list[re.Pattern[str]]:
    """Expands comma-separated / brace patterns into a flat list of compiled regexes."""
    patterns: list[re.Pattern[str]] = []
    for raw in _split_top_level_commas(pattern):
        raw = raw.strip()
        if not raw:
            continue
        for expanded in _expand_braces(raw):
            patterns.append(_glob_to_regex(expanded))
    return patterns


def _matches_any(rel_path: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(p.match(rel_path) for p in patterns)


# --- read_file ------------------------------------------------------------------------


def read_file(
    path: str,
    binary: bool = False,
    head: int | None = None,
    tail: int | None = None,
    line_numbers: bool = False,
) -> str:
    """Reads the complete contents of a file (UTF-8 text, or base64 if binary=true). 10MB limit unless head/tail is used."""
    path = validate_path(path, must_exist=True)
    if head is not None and tail is not None:
        raise ValueError("head and tail are mutually exclusive")
    if binary and (head is not None or tail is not None):
        raise ValueError("head/tail are not supported with binary=true")

    size = os.path.getsize(path)
    if binary:
        if size > MAX_READ_FILE_BYTES:
            raise ValueError(f"file exceeds the {MAX_READ_FILE_BYTES} byte limit ({size} bytes); no head/tail for binary reads")
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")

    if head is None and tail is None and size > MAX_READ_FILE_BYTES:
        raise ValueError(f"file exceeds the {MAX_READ_FILE_BYTES} byte limit ({size} bytes); use head or tail")

    with open(path, encoding="utf-8") as f:
        all_lines = f.readlines()

    if head is not None:
        selected = all_lines[:head]
        first_line_number = 1
    elif tail is not None:
        selected = all_lines[-tail:] if tail > 0 else []
        first_line_number = len(all_lines) - len(selected) + 1
    else:
        selected = all_lines
        first_line_number = 1

    if not line_numbers:
        return "".join(selected)

    return "".join(f"{first_line_number + idx:6d}| {line}" for idx, line in enumerate(selected))


# --- write_file -------------------------------------------------------------------------


def write_file(
    path: str,
    content: str,
    binary: bool = False,
    mkdir_parents: bool = False,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Creates a new file or overwrites an existing one with new content."""
    path = validate_path(path, must_exist=False)
    if not overwrite and os.path.exists(path):
        raise FileExistsError(f"path already exists and overwrite=false: {path}")
    if mkdir_parents:
        os.makedirs(os.path.dirname(path) or "/", exist_ok=True)

    if binary:
        data = base64.b64decode(content)
        with open(path, "wb") as f:
            f.write(data)
        written = len(data)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        written = len(content.encode("utf-8"))

    return {"path": path, "bytes_written": written}


# --- edit_file ----------------------------------------------------------------------------


def edit_file(
    path: str,
    old_text: str,
    new_text: str,
    backup: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Makes a targeted edit by replacing all literal occurrences of old_text with new_text in a file."""
    path = validate_path(path, must_exist=True)
    with open(path, encoding="utf-8") as f:
        original = f.read()

    count = original.count(old_text)
    if count == 0:
        raise ValueError(f"old_text not found in {path}")

    updated = original.replace(old_text, new_text)

    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=path,
            tofile=path,
        )
    )

    if dry_run:
        return {"path": path, "occurrences_replaced": count, "dry_run": True, "diff": diff}

    if backup:
        shutil.copy2(path, f"{path}.bak")

    with open(path, "w", encoding="utf-8") as f:
        f.write(updated)

    return {"path": path, "occurrences_replaced": count, "dry_run": False, "diff": diff}


# --- append_file --------------------------------------------------------------------------


def append_file(path: str, content: str, mkdir_parents: bool = False) -> dict[str, Any]:
    """Appends content to the end of a file, creating it if it doesn't exist. No automatic newline is added."""
    path = validate_path(path, must_exist=False)
    if mkdir_parents:
        os.makedirs(os.path.dirname(path) or "/", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(content)
    return {"path": path, "bytes_appended": len(content.encode("utf-8"))}


# --- delete_file --------------------------------------------------------------------------


def delete_file(path: str, confirm: bool) -> dict[str, Any]:
    """Permanently deletes a file. Requires confirm=true to prevent accidental deletion."""
    path = validate_path(path, must_exist=True)
    if confirm is not True:
        raise ValueError("confirm must be true to delete a file")
    os.remove(path)
    return {"path": path, "deleted": True}


# --- copy_file / move_file -----------------------------------------------------------------


def copy_file(source: str, destination: str, mkdir_parents: bool = False, overwrite: bool = False) -> dict[str, Any]:
    """Copies a file to a new location. destination must be a full file path, not a directory."""
    source = validate_path(source, must_exist=True)
    destination = validate_path(destination, must_exist=False)
    if os.path.isdir(destination):
        raise ValueError(f"destination must be a full file path, not a directory: {destination}")
    if not overwrite and os.path.exists(destination):
        raise FileExistsError(f"destination already exists and overwrite=false: {destination}")
    if mkdir_parents:
        os.makedirs(os.path.dirname(destination) or "/", exist_ok=True)
    shutil.copy2(source, destination)
    return {"source": source, "destination": destination}


def move_file(source: str, destination: str, mkdir_parents: bool = False, overwrite: bool = False) -> dict[str, Any]:
    """Moves or renames a file. destination must be a full file path, not a directory."""
    source = validate_path(source, must_exist=True)
    destination = validate_path(destination, must_exist=False)
    if os.path.isdir(destination):
        raise ValueError(f"destination must be a full file path, not a directory: {destination}")
    if not overwrite and os.path.exists(destination):
        raise FileExistsError(f"destination already exists and overwrite=false: {destination}")
    if mkdir_parents:
        os.makedirs(os.path.dirname(destination) or "/", exist_ok=True)
    if overwrite and os.path.exists(destination):
        os.remove(destination)
    shutil.move(source, destination)
    return {"source": source, "destination": destination}


# --- create_directory / delete_directory -----------------------------------------------------


def create_directory(path: str) -> dict[str, Any]:
    """Creates a directory (and any missing parents). Succeeds silently if it already exists."""
    path = validate_path(path, must_exist=False)
    os.makedirs(path, exist_ok=True)
    return {"path": path, "created": True}


def delete_directory(path: str, confirm: bool, force: bool = False) -> dict[str, Any]:
    """Removes a directory. Only empty directories unless force=true (recursive delete)."""
    path = validate_path(path, must_exist=True)
    if confirm is not True:
        raise ValueError("confirm must be true to delete a directory")
    if force:
        shutil.rmtree(path)
    else:
        os.rmdir(path)  # raises OSError if not empty
    return {"path": path, "deleted": True, "force": force}


# --- list_directory -------------------------------------------------------------------------


def list_directory(
    path: str,
    detail: Literal["minimal", "standard", "full"] = "standard",
    hidden: bool = True,
    count_only: bool = False,
) -> dict[str, Any]:
    """Lists the contents of a directory, with optional detail level and hidden-file filtering."""
    path = validate_path(path, must_exist=True)
    entries = sorted(os.listdir(path))
    if not hidden:
        entries = [e for e in entries if not e.startswith(".")]

    if count_only:
        files = dirs = 0
        for e in entries:
            full = os.path.join(path, e)
            if os.path.isdir(full):
                dirs += 1
            else:
                files += 1
        return {"path": path, "files": files, "directories": dirs, "total": len(entries)}

    result = []
    for e in entries:
        full = os.path.join(path, e)
        st = os.lstat(full)
        entry_type = "directory" if os.path.isdir(full) else ("symlink" if os.path.islink(full) else "file")
        item: dict[str, Any] = {"name": e}
        if detail == "minimal":
            result.append(item)
            continue
        item["type"] = entry_type
        item["size"] = st.st_size
        if detail == "full":
            item["modified"] = datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat()
            item["permissions"] = stat_module.filemode(st.st_mode)
        result.append(item)

    return {"path": path, "entries": result}


# --- find_files -----------------------------------------------------------------------------


def find_files(
    path: str,
    pattern: str,
    type: Literal["file", "directory", "any"] = "any",
    exclude: str = DEFAULT_FIND_EXCLUDE,
    max_depth: int | None = None,
    max_results: int = 200,
    count_only: bool = False,
) -> dict[str, Any]:
    """Finds files and directories by glob pattern (supports **, *, ?, [abc], {a,b,c})."""
    path = validate_path(path, must_exist=True)
    include_patterns = _compile_glob_patterns(pattern)
    exclude_patterns = _compile_glob_patterns(exclude) if exclude else []
    base_depth = path.rstrip("/").count("/")

    matches: list[str] = []
    count = 0
    for root, dirs, files in os.walk(path):
        rel_root = os.path.relpath(root, path)
        depth = root.rstrip("/").count("/") - base_depth

        def rel_of(name: str, rel_root: str = rel_root) -> str:
            return name if rel_root == "." else f"{rel_root}/{name}"

        # report this level's entries before any recursion-control pruning below,
        # so max_depth (which only limits how far os.walk descends) doesn't also
        # hide the directories actually sitting at that depth
        candidates: list[tuple[str, str]] = [(rel_of(d), "directory") for d in dirs]
        candidates.extend((rel_of(fname), "file") for fname in files)

        # prune in-place (the list os.walk itself watches) so it doesn't descend
        # into excluded directories or past max_depth
        dirs[:] = [d for d in dirs if not _matches_any(rel_of(d), exclude_patterns)]
        if max_depth is not None and depth + 1 >= max_depth:
            dirs[:] = []

        for rel, kind in candidates:
            if _matches_any(rel, exclude_patterns):
                continue
            if type != "any" and type != kind:
                continue
            if not _matches_any(rel, include_patterns):
                continue
            count += 1
            if len(matches) < max_results:
                matches.append(os.path.join(path, rel))

    if count_only:
        return {"path": path, "pattern": pattern, "count": count}
    return {"path": path, "pattern": pattern, "matches": matches, "truncated": count > len(matches)}


# --- grep -----------------------------------------------------------------------------------


def _is_probably_binary(sample: bytes) -> bool:
    return b"\x00" in sample


def _iter_grep_targets(path: str, include: str | None, exclude: str):
    include_patterns = _compile_glob_patterns(include) if include else None
    exclude_patterns = _compile_glob_patterns(exclude) if exclude else []

    if os.path.isfile(path):
        yield path
        return

    for root, dirs, files in os.walk(path):
        rel_root = os.path.relpath(root, path)
        dirs[:] = [
            d
            for d in dirs
            if not _matches_any(d if rel_root == "." else f"{rel_root}/{d}", exclude_patterns)
        ]
        for fname in files:
            rel = fname if rel_root == "." else f"{rel_root}/{fname}"
            if _matches_any(rel, exclude_patterns):
                continue
            if include_patterns is not None and not _matches_any(rel, include_patterns):
                continue
            yield os.path.join(root, fname)


def grep(
    path: str,
    pattern: str,
    case_sensitive: bool = False,
    context_lines: int = 0,
    count_only: bool = False,
    exclude: str = DEFAULT_GREP_EXCLUDE,
    include: str | None = None,
    max_results: int = 100,
    regex: bool = False,
) -> dict[str, Any]:
    """Searches file contents for matching lines (literal substring by default, or regex). Binary files are skipped."""
    path = validate_path(path, must_exist=True)
    flags = 0 if case_sensitive else re.IGNORECASE
    compiled = re.compile(pattern if regex else re.escape(pattern), flags)

    per_file_counts: dict[str, int] = {}
    results: list[dict[str, Any]] = []
    total = 0

    for fpath in _iter_grep_targets(path, include, exclude):
        try:
            with open(fpath, "rb") as f:
                sample = f.read(8192)
            if _is_probably_binary(sample):
                continue
            with open(fpath, encoding="utf-8", errors="strict") as f:
                lines = f.readlines()
        except (UnicodeDecodeError, OSError):
            continue

        file_matches = 0
        for i, line in enumerate(lines):
            if compiled.search(line):
                file_matches += 1
                total += 1
                if not count_only and len(results) < max_results:
                    lo = max(0, i - context_lines)
                    hi = min(len(lines), i + context_lines + 1)
                    context = "".join(lines[lo:hi])
                    results.append({"file": fpath, "line": i + 1, "match": line.rstrip("\n"), "context": context if context_lines else None})
        if file_matches:
            per_file_counts[fpath] = file_matches

    if count_only:
        return {"path": path, "pattern": pattern, "counts": per_file_counts, "total": total}
    return {"path": path, "pattern": pattern, "matches": results, "total": total, "truncated": total > len(results)}


# --- stat / exists / checksum / compare_files / patch_file ----------------------------------


def stat(path: str) -> dict[str, Any]:
    """Gets detailed metadata about a file or directory: size, timestamps, permissions, type."""
    path = validate_path(path, must_exist=True)
    st = os.lstat(path)
    is_link = os.path.islink(path)
    entry_type = "symlink" if is_link else ("directory" if os.path.isdir(path) else "file")
    result: dict[str, Any] = {
        "path": path,
        "type": entry_type,
        "size": st.st_size,
        "created": datetime.fromtimestamp(st.st_ctime, tz=UTC).isoformat(),
        "modified": datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat(),
        "accessed": datetime.fromtimestamp(st.st_atime, tz=UTC).isoformat(),
        "permissions": stat_module.filemode(st.st_mode),
        "mode_octal": oct(stat_module.S_IMODE(st.st_mode)),
    }
    if is_link:
        result["symlink_target"] = os.readlink(path)
    return result


def exists(path: str, type: Literal["file", "directory", "any"] = "any") -> dict[str, Any]:
    """Checks whether a path exists, optionally verifying it is a file or directory."""
    _check_absolute_syntax(path)
    try:
        resolved = validate_path(path, must_exist=True)
    except FileNotFoundError:
        # Mirrors the original: "doesn't exist" reports exists=False rather than
        # erroring; "outside allowed directories" (ValueError) still propagates.
        return {"path": path, "exists": False}
    present = True
    if type != "any":
        present = os.path.isfile(resolved) if type == "file" else os.path.isdir(resolved)
    return {"path": resolved, "exists": present}


def checksum(path: str, algorithm: Literal["md5", "sha256", "sha512"] = "sha256") -> dict[str, Any]:
    """Generates a hash/checksum of a file's contents, for integrity checks or comparison."""
    path = validate_path(path, must_exist=True)
    h = hashlib.new(algorithm)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return {"path": path, "algorithm": algorithm, "digest": h.hexdigest()}


def compare_files(file_a: str, file_b: str, context_lines: int = 3) -> dict[str, Any]:
    """Generates a unified diff between two files. Empty diff if files are identical."""
    file_a = validate_path(file_a, must_exist=True)
    file_b = validate_path(file_b, must_exist=True)
    with open(file_a, encoding="utf-8") as f:
        a_lines = f.readlines()
    with open(file_b, encoding="utf-8") as f:
        b_lines = f.readlines()
    diff = "".join(difflib.unified_diff(a_lines, b_lines, fromfile=file_a, tofile=file_b, n=context_lines))
    return {"file_a": file_a, "file_b": file_b, "diff": diff, "identical": diff == ""}


def patch_file(path: str, patch: str, reverse: bool = False, backup: bool = True) -> dict[str, Any]:
    """Applies a unified diff patch to a file (e.g. from edit_file's dry_run or compare_files)."""
    path = validate_path(path, must_exist=True)
    if backup:
        shutil.copy2(path, f"{path}.bak")

    fd, patch_path = tempfile.mkstemp(suffix=".patch")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(patch)
        cmd = ["patch"]
        if reverse:
            cmd.append("-R")
        cmd.extend(["-p0", path, patch_path])
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise ValueError(f"patch failed (exit {proc.returncode}): {proc.stdout}\n{proc.stderr}")
        return {"path": path, "applied": True, "reverse": reverse, "output": proc.stdout}
    finally:
        os.remove(patch_path)


# --- touch ------------------------------------------------------------------------------------


def touch(path: str, mkdir_parents: bool = False) -> dict[str, Any]:
    """Creates an empty file if it doesn't exist, or updates its modification time if it does."""
    path = validate_path(path, must_exist=False)
    if mkdir_parents:
        os.makedirs(os.path.dirname(path) or "/", exist_ok=True)
    existed = os.path.exists(path)
    Path(path).touch(exist_ok=True)
    if existed:
        os.utime(path, None)
    return {"path": path, "created": not existed}


# --- tail_follow (per-path cursor state, in-memory for the server process's lifetime) ----------


@dataclass
class _TailCursor:
    inode: int
    offset: int


class _TailFollowState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cursors: dict[str, _TailCursor] = {}

    def read_new_content(self, path: str, reset: bool) -> str:
        st = os.stat(path)
        with self._lock:
            cursor = self._cursors.get(path)
            if reset or cursor is None or cursor.inode != st.st_ino or st.st_size < cursor.offset:
                self._cursors[path] = _TailCursor(inode=st.st_ino, offset=st.st_size)
                return ""
            with open(path, encoding="utf-8", errors="replace") as f:
                f.seek(cursor.offset)
                new_content = f.read()
            self._cursors[path] = _TailCursor(inode=st.st_ino, offset=st.st_size)
            return new_content


_tail_follow_state = _TailFollowState()


def tail_follow(path: str, reset: bool = False) -> dict[str, Any]:
    """Returns new content appended to a file since the last call, maintaining a per-path cursor."""
    path = validate_path(path, must_exist=True)
    content = _tail_follow_state.read_new_content(path, reset)
    return {"path": path, "content": content}


# --- tree -----------------------------------------------------------------------------------


def _tree_lines(base: str, max_depth: int | None, exclude_names: set[str], hidden: bool, prefix: str, depth: int) -> list[str]:
    if max_depth is not None and depth >= max_depth:
        return []
    try:
        entries = sorted(os.listdir(base))
    except PermissionError:
        return [f"{prefix}[permission denied]"]
    if not hidden:
        entries = [e for e in entries if not e.startswith(".")]
    entries = [e for e in entries if e not in exclude_names]

    lines = []
    for i, name in enumerate(entries):
        full = os.path.join(base, name)
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        is_dir = os.path.isdir(full)
        lines.append(f"{prefix}{connector}{name}{'/' if is_dir else ''}")
        if is_dir:
            extension = "    " if is_last else "│   "
            lines.extend(_tree_lines(full, max_depth, exclude_names, hidden, prefix + extension, depth + 1))
    return lines


def tree(
    path: str,
    max_depth: int | None = None,
    exclude: str = DEFAULT_TREE_EXCLUDE,
    hidden: bool = False,
) -> dict[str, Any]:
    """Gets a recursive tree view of files and directories."""
    path = validate_path(path, must_exist=True)
    exclude_names = {e.strip() for e in exclude.split(",") if e.strip()}
    lines = [path]
    lines.extend(_tree_lines(path, max_depth, exclude_names, hidden, "", 0))
    return {"path": path, "tree": "\n".join(lines)}


# --- read_lines -----------------------------------------------------------------------------


def read_lines(path: str, start_line: int, end_line: int, line_numbers: bool = True) -> str:
    """Reads a specific 1-indexed, inclusive range of lines from a file. end_line=-1 reads to end of file."""
    path = validate_path(path, must_exist=True)
    if start_line < 1:
        raise ValueError("start_line is 1-indexed and must be >= 1")
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    if end_line == -1:
        selected = lines[start_line - 1 :]
    else:
        if end_line < start_line:
            raise ValueError("end_line must be >= start_line (or -1 for end of file)")
        selected = lines[start_line - 1 : end_line]

    if not line_numbers:
        return "".join(selected)

    out = []
    for idx, line in enumerate(selected):
        out.append(f"{start_line + idx:6d}| {line}")
    return "".join(out)


# --- read_document --------------------------------------------------------------------------


def _read_pdf(path: str) -> str:
    proc = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise ValueError(f"pdftotext failed (exit {proc.returncode}): {proc.stderr}")
    return proc.stdout


def _read_xlsx(path: str) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    sections = []
    for sheet in wb.worksheets:
        rows = []
        for row in sheet.iter_rows(values_only=True):
            rows.append("\t".join("" if v is None else str(v) for v in row))
        sections.append(f"--- Sheet: {sheet.title} ---\n" + "\n".join(rows))
    return "\n\n".join(sections)


def _read_xls(path: str) -> str:
    import xlrd

    wb = xlrd.open_workbook(path)
    sections = []
    for sheet in wb.sheets():
        rows = []
        for r in range(sheet.nrows):
            rows.append("\t".join("" if c in (None, "") else str(c) for c in sheet.row_values(r)))
        sections.append(f"--- Sheet: {sheet.name} ---\n" + "\n".join(rows))
    return "\n\n".join(sections)


def _read_docx(path: str) -> str:
    import docx

    document = docx.Document(path)
    return "\n".join(p.text for p in document.paragraphs)


_DOCUMENT_READERS = {
    ".pdf": _read_pdf,
    ".xlsx": _read_xlsx,
    ".xls": _read_xls,
    ".docx": _read_docx,
}


def read_document(path: str) -> str:
    """Extracts text content from a PDF, XLSX, XLS, or DOCX file for LLM consumption."""
    path = validate_path(path, must_exist=True)
    ext = os.path.splitext(path)[1].lower()
    reader = _DOCUMENT_READERS.get(ext)
    if reader is None:
        raise ValueError(f"unsupported document type {ext!r}; supported: {sorted(_DOCUMENT_READERS)} (use read_file for CSV/TSV)")
    return reader(path)


# --- get_hostname -----------------------------------------------------------------------------


def get_hostname() -> dict[str, str]:
    """Returns the short hostname of the machine running this server, with any DNS domain stripped."""
    fqdn = socket.getfqdn()
    short = fqdn.split(".")[0] if fqdn else socket.gethostname().split(".")[0]
    return {"hostname": short, "fqdn": fqdn}
