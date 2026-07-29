# Contributing

## Development setup

```bash
git clone https://github.com/smiffy-online/filesystem_supertool2.git
cd filesystem_supertool2
uv sync --group dev
```

## Running tests

```bash
uv run pytest                                    # fast unit tests, no network
uv run pytest -m integration tests/test_smoke_semantic.py  # needs network + uv/uvx
```

The integration suite starts a real Pyright language server process against a
throwaway sample project. It needs network access on first run (to fetch
Pyright via `uvx`) and Node.js on `PATH` (Pyright's own runtime dependency).

## Linting

```bash
uv run ruff check .
```

`src/filesystem_supertool2/solidlsp/` (vendored, extracted from
[oraios/serena](https://github.com/oraios/serena)) is excluded from ruff --
see the comment in `pyproject.toml`. If you touch
`solidlsp/_compat.py` (the compatibility shims that *are* ours), lint it
explicitly, since it lives inside the excluded directory:

```bash
uv run ruff check src/filesystem_supertool2/solidlsp/_compat.py
```

## Code style

- Python >= 3.13, type-hinted.
- No comments explaining *what* code does -- only *why*, where non-obvious.
- New filesystem/semantic tools: match parameter names and defaults against
  existing tools for consistency, and add both a docstring (MCP surfaces it
  as the tool's `description`) and unit tests.

## Adding a language server

`solidlsp` supports 40+ languages upstream; this project currently vendors
only the Python (Pyright) implementation, trimmed from the full extraction to
keep the dependency footprint minimal. To add another language:

1. Copy the corresponding file from upstream `oraios/serena`'s
   `src/solidlsp/language_servers/` into
   `src/filesystem_supertool2/solidlsp/language_servers/`.
2. Rewrite its internal `from solidlsp.X import Y` imports to
   `from filesystem_supertool2.solidlsp.X import Y` (this package nests
   `solidlsp` under `filesystem_supertool2`, unlike upstream, where it's a
   top-level package).
3. Check it doesn't pull in `serena.util`/`sensai-utils` symbols beyond what
   `solidlsp/_compat.py` already provides; extend `_compat.py` if it does.
4. Add its third-party dependencies to `pyproject.toml`.

## Pull requests

Open a PR against `main`. Include test coverage for new behaviour.
