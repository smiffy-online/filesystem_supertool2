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

`uv` itself installs per-user by default (`~/.local/bin`). For a shared,
system-wide binary on a multi-user machine instead:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
```

Each user's Python interpreter downloads, tool installs (`uvx`), and cache
still land in their own home directory by default either way -- uv doesn't
document a supported pattern for sharing those too, so this only solves "one
`uv` binary for everyone," not "one shared cache/interpreter set."

```bash
git clone https://github.com/smiffy-online/filesystem_supertool2.git
cd filesystem_supertool2
uv sync
```

### Dependency footprint (known tradeoff vs the original)

The original (Node/TypeScript) Filesystem Supertool has **zero** npm
dependencies -- no `dependencies` block in `package.json` at all. It hand-rolls
its own stdio/JSON-RPC framing, path validation, and diffing rather than
depending on the official `@modelcontextprotocol/sdk` or any utility library,
so `node server.js` runs immediately on any machine with Node installed, no
package-fetch step, ever.

This rewrite does not match that bar, by deliberate choice (see thread #360):
it uses the official `mcp` PyPI package for the protocol layer rather than
hand-rolling JSON-RPC-over-stdio against stdlib, which pulls in its own
transitive tree (pydantic, starlette, anyio, httpx, jsonschema, uvicorn,
opentelemetry, etc.). `fs_tools.py` itself has no third-party dependencies
(the document-parsing libraries for `read_document` are lazily imported, only
needed if that tool is actually called) -- the footprint is entirely the MCP
SDK and solidlsp's own requirements (`lsprotocol`, `overrides`, `pathspec`,
`psutil`, `requests`).

Practical consequences:
- **~42 resolved packages** (`uv tree --no-dev`), vs 0 for the original.
- **First run per machine needs network access** to resolve and cache these
  from PyPI (`uv run` does this automatically; subsequent runs use the cache).
  The original never needs this at all.
- Semantic tools add a further, separate fetch on first use: `uvx` pulls the
  pinned `pyright` PyPI package, which itself fetches its actual (Node-based)
  language server binary. This is on top of, not instead of, the Python
  dependency resolution above.
- Once `uv` is installed system-wide (see below) and the caches are warm, this
  behaves the same as the original in practice -- the gap is specifically
  about first-run-per-machine network dependency and package count, not
  ongoing runtime behaviour.

If a fully hand-rolled, zero-third-party-dependency protocol layer becomes a
hard requirement later, that would mean dropping the `mcp` package and
reimplementing JSON-RPC-over-stdio (framing, `initialize` handshake,
`tools/list`, `tools/call`) directly -- a substantial rewrite of `server.py`,
not a small change.

### Allowed directories (required)

Matching the original Filesystem Supertool's security boundary exactly: every
path-accepting tool (filesystem and semantic alike) is restricted to a
configured set of allowed root directories -- symlink targets and
not-yet-existing paths' nearest existing ancestor are checked too, not just
the literal requested path. The server refuses to start without at least one.

Configure via `ALLOWED_DIRS` (comma-separated, takes priority) or as CLI
arguments (each may itself be comma-separated):

```bash
ALLOWED_DIRS=/home/user,/workspaces uv run filesystem-supertool2
# or
uv run filesystem-supertool2 /home/user,/workspaces
```

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
      "args": ["run", "--directory", "/absolute/path/to/filesystem_supertool2", "filesystem-supertool2", "/home/user,/workspaces"]
    }
  }
}
```

### Claude Code

```bash
claude mcp add filesystem-supertool2 -s user -- uv run --directory /absolute/path/to/filesystem_supertool2 filesystem-supertool2 /home/user,/workspaces
```

`-s user` makes it available across all projects on this machine (each host
running its own CC instance needs this run locally, with the `--directory`
and allowed-directories args adjusted to that host's clone and paths -- there
is no cross-host config sync). See `claude mcp add --help` for the other
scope options (`local`, `project`) and flags (`-e` for env vars instead of
CLI args, etc.).

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
