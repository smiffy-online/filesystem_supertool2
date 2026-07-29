# server.py

from __future__ import annotations

import atexit
import os
import sys

from mcp.server.mcpserver import MCPServer

from filesystem_supertool2 import fs_tools, semantic_tools

FS_TOOLS = (
    fs_tools.read_file,
    fs_tools.write_file,
    fs_tools.edit_file,
    fs_tools.append_file,
    fs_tools.delete_file,
    fs_tools.copy_file,
    fs_tools.move_file,
    fs_tools.create_directory,
    fs_tools.delete_directory,
    fs_tools.list_directory,
    fs_tools.find_files,
    fs_tools.grep,
    fs_tools.stat,
    fs_tools.checksum,
    fs_tools.compare_files,
    fs_tools.patch_file,
    fs_tools.exists,
    fs_tools.touch,
    fs_tools.tail_follow,
    fs_tools.tree,
    fs_tools.read_lines,
    fs_tools.read_document,
    fs_tools.get_hostname,
)

SEMANTIC_TOOLS = (
    semantic_tools.find_symbol,
    semantic_tools.request_definition,
    semantic_tools.request_references,
    semantic_tools.request_document_overview,
    semantic_tools.request_diagnostics,
    semantic_tools.request_rename,
    semantic_tools.replace_symbol_body,
    semantic_tools.insert_before_symbol,
    semantic_tools.insert_after_symbol,
)


def build_server() -> MCPServer:
    mcp = MCPServer(
        name="filesystem-supertool2",
        title="Filesystem Supertool v2",
        description="Filesystem operations plus LSP-powered semantic code tools, over STDIO.",
        version="0.1.0",
    )
    for fn in FS_TOOLS:
        mcp.add_tool(fn)
    for fn in SEMANTIC_TOOLS:
        mcp.add_tool(fn)
    return mcp


def _parse_allowed_dirs(env_value: str | None, cli_args: list[str]) -> list[str]:
    """
    Matches the original (Node/TypeScript) Filesystem Supertool's interface exactly:
    ``ALLOWED_DIRS`` env var (comma-separated) takes priority if set and non-empty,
    otherwise directories are taken from CLI args (each arg may itself be
    comma-separated, e.g. a single ``/home/user,/workspaces`` argument).
    """
    if env_value:
        dirs = [d.strip() for d in env_value.split(",") if d.strip()]
        if dirs:
            return dirs

    dirs = []
    for arg in cli_args:
        if arg.startswith("-"):
            continue
        if "," in arg:
            dirs.extend(d.strip() for d in arg.split(",") if d.strip())
        else:
            dirs.append(arg)
    return dirs


def main() -> None:
    allowed_dirs = _parse_allowed_dirs(os.environ.get("ALLOWED_DIRS"), sys.argv[1:])
    if not allowed_dirs:
        print("Error: No allowed directories specified", file=sys.stderr)
        print("Set ALLOWED_DIRS environment variable or pass directories as arguments", file=sys.stderr)
        print("Example: ALLOWED_DIRS=/home/user,/workspaces uv run filesystem-supertool2", file=sys.stderr)
        sys.exit(1)
    fs_tools.set_allowed_directories(allowed_dirs)

    atexit.register(semantic_tools.shutdown_all_servers)
    mcp = build_server()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
