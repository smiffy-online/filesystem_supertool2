"""
End-to-end smoke test for the solidlsp extraction: starts a real Pyright language
server (via uvx) against a small sample project and exercises find_symbol.

Needs network access (uvx fetches the pinned pyright PyPI package, and pyright
itself fetches its Node-based language server binary, on first run) and a
working `uv`/`uvx` on PATH. Marked "integration" and excluded from the default
test run; invoke explicitly with:

    uv run pytest -m integration tests/test_smoke_semantic.py
"""

from __future__ import annotations

import pytest

from filesystem_supertool2 import semantic_tools

pytestmark = pytest.mark.integration

SAMPLE_SOURCE = '''\
class Greeter:
    def __init__(self, name: str) -> None:
        self.name = name

    def greet(self) -> str:
        return f"Hello, {self.name}!"


def main() -> None:
    g = Greeter("world")
    print(g.greet())


if __name__ == "__main__":
    main()
'''


@pytest.fixture
def sample_project(tmp_path):
    (tmp_path / "main.py").write_text(SAMPLE_SOURCE)
    root = str(tmp_path)
    yield root
    semantic_tools.shutdown_all_servers()


def test_find_symbol_workspace_wide(sample_project):
    result = semantic_tools.find_symbol(sample_project, "Greeter")
    assert any(s["name"] == "Greeter" for s in result)


def test_find_symbol_scoped_to_file(sample_project):
    result = semantic_tools.find_symbol(sample_project, "Greeter/greet", relative_path="main.py")
    assert len(result) == 1
    assert result[0]["name"] == "greet"
    assert result[0]["relative_path"] == "main.py"


def test_find_symbol_not_found_returns_empty(sample_project):
    result = semantic_tools.find_symbol(sample_project, "NoSuchSymbol", relative_path="main.py")
    assert result == []


def test_document_overview(sample_project):
    overview = semantic_tools.request_document_overview(sample_project, "main.py")
    names = {s["name"] for s in overview}
    assert {"Greeter", "main"} <= names


def test_diagnostics_clean_file_has_none(sample_project):
    diagnostics = semantic_tools.request_diagnostics(sample_project, "main.py")
    assert diagnostics == []


def test_replace_symbol_body_round_trips(sample_project):
    result = semantic_tools.replace_symbol_body(
        sample_project,
        "main",
        "main.py",
        'def main() -> None:\n    print("replaced")\n',
    )
    assert result["name_path"] == "main"

    from filesystem_supertool2 import fs_tools

    content = fs_tools.read_file(f"{sample_project}/main.py")
    assert 'print("replaced")' in content
    assert "Greeter" in content  # rest of the file must survive the edit


def test_insert_before_symbol_round_trips(sample_project):
    from filesystem_supertool2 import fs_tools

    semantic_tools.insert_before_symbol(sample_project, "main", "main.py", "# marker-before\n")
    content = fs_tools.read_file(f"{sample_project}/main.py")
    assert "# marker-before\ndef main() -> None:" in content


def test_insert_after_symbol_round_trips(sample_project):
    from filesystem_supertool2 import fs_tools

    semantic_tools.insert_after_symbol(sample_project, "Greeter", "main.py", "# marker-after\n")
    content = fs_tools.read_file(f"{sample_project}/main.py")
    assert "# marker-after" in content
    # inserted after the Greeter class, before "def main"
    assert content.index("# marker-after") < content.index("def main")
