import pytest

from filesystem_supertool2.server import _parse_allowed_dirs, build_server, main


def test_parse_allowed_dirs_from_env_comma_separated():
    assert _parse_allowed_dirs("/home/user,/workspaces", []) == ["/home/user", "/workspaces"]


def test_parse_allowed_dirs_env_takes_priority_over_cli():
    assert _parse_allowed_dirs("/from/env", ["/from/cli"]) == ["/from/env"]


def test_parse_allowed_dirs_falls_back_to_cli_when_env_empty():
    assert _parse_allowed_dirs(None, ["/home/user", "/workspaces"]) == ["/home/user", "/workspaces"]
    assert _parse_allowed_dirs("", ["/home/user"]) == ["/home/user"]


def test_parse_allowed_dirs_cli_arg_can_be_comma_separated():
    assert _parse_allowed_dirs(None, ["/home/user,/workspaces"]) == ["/home/user", "/workspaces"]


def test_parse_allowed_dirs_ignores_flag_style_args():
    assert _parse_allowed_dirs(None, ["--stdio", "/home/user"]) == ["/home/user"]


def test_parse_allowed_dirs_empty_when_nothing_given():
    assert _parse_allowed_dirs(None, []) == []


def test_main_exits_when_no_allowed_dirs(monkeypatch, capsys):
    monkeypatch.delenv("ALLOWED_DIRS", raising=False)
    monkeypatch.setattr("sys.argv", ["filesystem-supertool2"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1
    assert "No allowed directories specified" in capsys.readouterr().err


def test_build_server_registers_all_tools():
    mcp = build_server()
    tools = mcp._tool_manager.list_tools()
    assert len(tools) == 32
    assert all(t.description.strip() for t in tools)
