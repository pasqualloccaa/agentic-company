import json
import sys
from pathlib import Path

import pytest

from agentic_company.agents.claude_cli import (
    AgentError, ClaudeAgent, ClaudeSettings, UsageLimitReached, allowed_tools, build_command,
    isolation_problems, mcp_config,
)
from agentic_company.tools import TOOLS

FAKE = Path(__file__).parent / "fixtures" / "fake_claude.py"


@pytest.fixture
def agent(tmp_path, monkeypatch):
    log = tmp_path / "call.json"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    a = ClaudeAgent(ClaudeSettings(executable=[sys.executable, str(FAKE)], model="sonnet",
                                   sandbox_root=tmp_path / "sandbox"), tmp_path / "run" / "agents")
    a.log = log  # type: ignore[attr-defined]
    return a


def test_command_isolates_the_agent(tmp_path):
    cmd = build_command(ClaudeSettings(executable=[sys.executable]), tmp_path / "s.md", tmp_path / "m.json")
    assert "-p" in cmd and "--strict-mcp-config" in cmd and "--no-session-persistence" in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--allowedTools") + 1].split(",") == [f"mcp__sim__{t}" for t in TOOLS]
    assert "--disable-slash-commands" in cmd and "--safe-mode" not in cmd  # safe mode drops our MCP server
    assert not any("dangerously" in c for c in cmd)


def test_command_passes_the_agent_settings_file(tmp_path):
    cmd = build_command(ClaudeSettings(executable=[sys.executable]), tmp_path / "s.md", tmp_path / "m.json")
    settings = json.loads(Path(cmd[cmd.index("--settings") + 1]).read_text(encoding="utf-8"))
    assert settings["autoMemoryEnabled"] is False
    assert settings["enabledPlugins"] == {"finance@synced": False, "data@synced": False,
                                          "cowork-plugin-management@synced": False}


def test_separate_config_dir_is_set_for_the_agent_process_only(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    log = tmp_path / "call.json"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    a = ClaudeAgent(ClaudeSettings(executable=[sys.executable, str(FAKE)], sandbox_root=tmp_path / "sb",
                                   config_dir=tmp_path / "cfg"), tmp_path / "run" / "agents")
    a.bind_run("mvp_s42_r1")
    a.turn("uffici", 1, "s", "b", "http://x")
    call = json.loads(log.read_text(encoding="utf-8"))
    assert call["config_dir"] == str(tmp_path / "cfg")
    assert Path(call["cwd"]) == tmp_path / "sb" / "mvp_s42_r1" / "uffici"   # one sandbox per run
    import os
    assert "CLAUDE_CONFIG_DIR" not in os.environ


def test_auto_memory_must_be_off():
    meta = {"tools": allowed_tools(), "mcp_servers": [{"name": "sim", "status": "connected"}],
            "memory_paths": {"auto": "C:/x/memory/"}}
    assert any("auto-memory" in p for p in isolation_problems(meta))


def test_permission_bypass_is_refused(tmp_path):
    settings = ClaudeSettings(executable=[sys.executable], extra_flags=("--dangerously-skip-permissions",))
    with pytest.raises(ValueError):
        build_command(settings, tmp_path / "s.md", tmp_path / "m.json")


def test_mcp_config_points_to_the_role_endpoint():
    assert mcp_config("http://127.0.0.1:1/mcp") == {"mcpServers": {"sim": {"type": "http",
                                                                           "url": "http://127.0.0.1:1/mcp"}}}


def test_sandbox_below_a_claude_md_is_refused(tmp_path, monkeypatch):
    (tmp_path / "CLAUDE.md").write_text("# segreto", encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    a = ClaudeAgent(ClaudeSettings(executable=[sys.executable, str(FAKE)], sandbox_root=tmp_path / "sb"),
                    tmp_path / "run" / "agents")
    with pytest.raises(AgentError, match="not clean"):
        a.turn("uffici", 1, "s", "b", "http://x")


def test_builtin_plugins_are_tolerated_personal_ones_are_not():
    base = {"tools": allowed_tools(), "mcp_servers": [{"name": "sim", "status": "connected"}]}
    assert isolation_problems({**base, "plugins": [{"name": "cc-plugin-telemetry", "path": "builtin"}],
                               "agents": ["general-purpose", "architect"]}) == []
    problems = isolation_problems({**base, "plugins": [{"name": "finance", "path": "C:/x/finance"}]})
    assert problems == ["unexpected plugins: ['finance']"]


def test_turn_passes_prompts_and_parses_result(agent, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    r = agent.turn("uffici", 3, "SISTEMA", "BRIEFING", "http://127.0.0.1:9/mcp")
    assert r.text == "Ho controllato i conti." and r.usage["output_tokens"] == 5
    assert r.meta["model"] == "fake-model" and r.meta["tool_uses"][0]["name"] == "mcp__sim__get_kpis"
    assert isolation_problems(r.meta) == []
    call = json.loads(agent.log.read_text(encoding="utf-8"))
    assert call["stdin"] == "BRIEFING"                       # briefing via stdin, no length limit
    cwd = Path(call["cwd"])
    assert cwd.parent.parent == tmp_path / "sandbox" and cwd.name == "uffici" and not any(cwd.iterdir())
    argv = call["argv"]
    assert Path(argv[argv.index("--system-prompt-file") + 1]).read_text(encoding="utf-8") == "SISTEMA"
    assert argv[argv.index("--model") + 1] == "sonnet"
    stream = agent.workdir / "uffici" / "w03_stream.jsonl"
    assert '"subtype": "init"' in stream.read_text(encoding="utf-8")   # raw transcript kept


def test_relative_workdir_still_finds_prompt_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    a = ClaudeAgent(ClaudeSettings(executable=[sys.executable, str(FAKE)], sandbox_root=tmp_path / "sb"),
                    Path("out") / "agents")
    assert a.turn("uffici", 1, "s", "b", "http://x").text == "Ho controllato i conti."


def test_usage_limit_is_detected(agent, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
    with pytest.raises(UsageLimitReached):
        agent.turn("cda", 4, "s", "b", "http://x")


def test_other_failures_are_errors_not_limits(agent, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "error")
    with pytest.raises(AgentError):
        agent.turn("cda", 4, "s", "b", "http://x")


def test_max_turns_ends_the_turn_without_failing(agent, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "max_turns")
    r = agent.turn("uffici", 1, "s", "b", "http://x")
    assert r.meta["subtype"] == "error_max_turns"


def test_leaky_session_is_reported(agent, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "leaky")
    problems = isolation_problems(agent.turn("uffici", 1, "s", "b", "http://x").meta)
    assert any("Bash" in p and "gmail" in p for p in problems)


def test_isolation_checks_server_status_and_denials():
    meta = {"tools": allowed_tools(), "mcp_servers": [{"name": "sim", "status": "failed"}],
            "permission_denials": [{"tool_name": "mcp__sim__set_price"}]}
    problems = isolation_problems(meta)
    assert any("failed" in p for p in problems) and any("denials" in p for p in problems)
    assert isolation_problems({}) == ["init event missing: cannot verify the tool list"]
