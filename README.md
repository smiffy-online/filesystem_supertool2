# Filesystem Supertool v2

A Python [MCP](https://modelcontextprotocol.io) server combining filesystem
operations with LSP-powered semantic code intelligence, served over STDIO for
Claude Desktop and Claude Code.

It's a from-scratch Python rewrite of the original (Node/TypeScript)
Filesystem Supertool, with a second capability layer added on top: semantic
code tools (find symbol, references, definitions, rename, diagnostics,
symbol-body editing) powered by a real language server (currently Python via
[Pyright](https://github.com/microsoft/pyright)), available when a language
server is running for the project and gracefully absent otherwise.

## Status

Functional. All filesystem tools and the initial (Python-only) semantic tool
set are implemented and tested; see [Testing](#testing) below. Not yet
published as a package -- run from source via `uv`.

## Tools

### Filesystem (23)

`read_file`, `write_file`, `edit_file`, `append_file`, `delete_file`,
`copy_file`, `move_file`, `create_directory`, `delete_directory`,
`list_directory`, `find_files`, `grep`, `stat`, `checksum`, `compare_files`,
`patch_file`, `exists`, `touch`, `tail_follow`, `tree`, `read_lines`,
`read_document` (PDF/XLSX/XLS/DOCX text extraction), `get_hostname`.

### Semantic (9, Python only for now)

`find_symbol`, `request_definition`, `request_references`,
`request_document_overview`, `request_diagnostics`, `request_rename`,
`replace_symbol_body`, `insert_before_symbol`, `insert_after_symbol`.

Backed by [`solidlsp`](https://github.com/oraios/serena) (see
[Architecture](#architecture) below), extracted and adapted into this
project rather than depended on.

## Installation

Requires [`uv`](https://docs.astral.sh/uv/) and Python >= 3.13 (uv will fetch
the interpreter if it isn't already installed). The Python semantic tools
additionally need [`uvx`](https://docs.astral.sh/uv/guides/tools/) (bundled
with `uv`) to fetch Pyright on first use, and Node.js on `PATH` (Pyright's
own runtime dependency).

```bash
git clone https://github.com/smiffy-online/filesystem_supertool2.git
cd filesystem_supertool2
uv sync
```

Run directly:

```bash
uv run filesystem-supertool2
```

This starts the MCP server on STDIO.

### Claude Desktop

This repo ships an [MCPB](https://github.com/anthropics/mcpb) manifest
(`manifest.json`, schema version 0.4, validated against the official `mcpb`
CLI). Either package it as a `.mcpb` bundle for Claude Desktop's extension
installer, or add an equivalent entry directly to your
`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "filesystem-supertool2": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/filesystem_supertool2", "filesystem-supertool2"]
    }
  }
}
```

### Claude Code

Add via `claude mcp add`, or an equivalent entry in your MCP settings,
pointing at the same `uv run` command.

## Architecture

- `src/filesystem_supertool2/fs_tools.py` -- filesystem operations, plain
  Python, reimplemented tool-for-tool against the original Filesystem
  Supertool's feature set.
- `src/filesystem_supertool2/semantic_tools.py` -- semantic code tools,
  built on `solidlsp`'s language-server-client primitives.
- `src/filesystem_supertool2/server.py` -- MCP server wiring (`MCPServer`
  from the `mcp` package), STDIO transport.
- `src/filesystem_supertool2/solidlsp/` -- extracted from
  [oraios/serena](https://github.com/oraios/serena)'s `src/solidlsp/`
  (itself a fork of Microsoft's
  [multilspy](https://github.com/microsoft/multilspy)). This is a fork/adopt,
  not a tracked dependency: taken as a base, adapted, and diverged as needed.
  Trimmed to the Python/Pyright language server only for now; other
  languages can be re-added from upstream as needed. See
  [`solidlsp/NOTICE.md`](src/filesystem_supertool2/solidlsp/NOTICE.md) for
  the full attribution and licensing detail, and `solidlsp/_compat.py` for
  the small number of `serena`/`sensai-utils` call sites that were inlined
  or reimplemented locally so this package has no runtime dependency on
  either.

One language server process is started per project root the first time a
semantic tool is used against it, and kept running for the life of the MCP
server process.

## Testing

```bash
uv run pytest              # fast unit tests (fs_tools.py), no network needed
uv run ruff check .         # linting (excludes the vendored solidlsp/ package)
```

A separate integration suite exercises the semantic tools end-to-end against
a real Pyright process. It needs network access (to fetch Pyright via `uvx`
on first run) and is excluded from the default test run:

```bash
uv run pytest -m integration tests/test_smoke_semantic.py
```

## License

MIT -- see [LICENSE](LICENSE). Includes MIT-licensed code adapted from
`solidlsp`/serena; see
[`solidlsp/NOTICE.md`](src/filesystem_supertool2/solidlsp/NOTICE.md).
