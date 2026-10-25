"""One agent turn = one non-interactive Claude Code process (`claude -p`).

The process is isolated: no built-in tools (--tools ""), only the simulation MCP
server (--mcp-config + --strict-mcp-config), an explicit allow-list of its tools
(--allowedTools), no skills or slash commands, no saved session, an empty
working directory. --dangerously-skip-permissions is never used.

The stream-json output is parsed for the init event (model, version, tools and
MCP servers actually loaded, used to verify isolation) and the final result
(text, usage, permission denials). Usage limits raise UsageLimitReached so the
runner can pause and resume.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..engine import ROOT
from ..tools import TOOLS

SERVER_NAME = "sim"
FORBIDDEN_FLAGS = ("--dangerously-skip-permissions", "--allow-dangerously-skip-permissions")
LIMIT_PATTERN = re.compile(r"usage limit|rate.?limit|hit your limit|limit reached|limit will reset|"
                           r"out of (extra )?usage|quota", re.IGNORECASE)


class UsageLimitReached(Exception):
    def __init__(self, message: str, reset_hint: str = ""):
        super().__init__(message)
        self.reset_hint = reset_hint


class AgentError(RuntimeError):
    pass


@dataclass
class TurnResult:
    text: str
    usage: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClaudeSettings:
    executable: str | list[str] = "claude"
    model: str | None = None
    max_turns: int = 40
    timeout_s: int = 1200
    extra_flags: tuple[str, ...] = ()   # not --safe-mode: it also drops the --mcp-config server
    # working directories live outside the repo: Claude Code reads every CLAUDE.md above its cwd
    sandbox_root: Path = Path(tempfile.gettempdir()) / "agentic-company-sandbox"
    # separate CLAUDE_CONFIG_DIR: no personal plugins, hooks, agents or memory of the author
    config_dir: Path | None = None
    # disables the plugins synced from claude.ai and auto-memory
    settings_file: Path = ROOT / "config" / "agent_settings.json"


def allowed_tools() -> list[str]:
    return [f"mcp__{SERVER_NAME}__{name}" for name in TOOLS]


def mcp_config(url: str) -> dict[str, Any]:
    return {"mcpServers": {SERVER_NAME: {"type": "http", "url": url}}}


def _executable(exe: str | list[str]) -> list[str]:
    if isinstance(exe, list):
        return list(exe)
    resolved = shutil.which(exe)
    if resolved is None:
        raise AgentError(f"Claude Code executable not found: {exe}")
    return [resolved]


def build_command(settings: ClaudeSettings, system_prompt_file: Path, mcp_config_file: Path) -> list[str]:
    cmd = _executable(settings.executable) + [
        "-p",
        "--output-format", "stream-json", "--verbose",
        "--system-prompt-file", str(system_prompt_file),
        "--mcp-config", str(mcp_config_file), "--strict-mcp-config",
        "--tools", "",
        "--allowedTools", ",".join(allowed_tools()),
        "--disable-slash-commands",
        "--no-session-persistence",
        "--max-turns", str(settings.max_turns),
        "--settings", str(Path(settings.settings_file).resolve()),
        *settings.extra_flags,
    ]
    if settings.model:
        cmd += ["--model", settings.model]
    if any(flag in cmd for flag in FORBIDDEN_FLAGS):
        raise ValueError("Permission bypass flags are not allowed")
    return cmd


def parse_stream(stdout: str) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]]]:
    """Return (init event, result event, tool_use blocks) from stream-json output."""
    init: dict[str, Any] = {}
    result = None
    tool_uses: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            init = event
        elif event.get("type") == "result":
            result = event
        elif event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []) or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_uses.append({"name": block.get("name"), "input": block.get("input")})
    return init, result, tool_uses


def isolation_problems(meta: dict[str, Any]) -> list[str]:
    """Check from the init event that the agent saw only the simulation server."""
    problems = []
    allowed = set(allowed_tools())
    tools = meta.get("tools")
    if tools is None:
        problems.append("init event missing: cannot verify the tool list")
    else:
        extra = sorted(set(tools) - allowed)
        if extra:
            problems.append(f"unexpected tools: {extra}")
        missing = sorted(allowed - set(tools))
        if missing:
            problems.append(f"simulation tools not loaded: {missing}")
    for server in meta.get("mcp_servers") or []:
        if server.get("name") != SERVER_NAME:
            problems.append(f"unexpected MCP server: {server.get('name')}")
        elif server.get("status") != "connected":
            problems.append(f"simulation MCP server status: {server.get('status')}")
    # subagent definitions may be listed, but without the Agent tool they cannot run;
    # plugins built into Claude Code itself are present in any configuration
    if meta.get("skills"):
        problems.append(f"unexpected skills: {meta['skills']}")
    plugins = [p.get("name") for p in meta.get("plugins") or [] if p.get("path") != "builtin"]
    if plugins:
        problems.append(f"unexpected plugins: {plugins}")
    if meta.get("memory_paths"):
        problems.append(f"auto-memory is on: {meta['memory_paths']}")
    if meta.get("permission_denials"):
        problems.append(f"permission denials: {meta['permission_denials']}")
    return problems


def memory_files_above(folder: Path) -> list[Path]:
    """Project memory files Claude Code would load when started in `folder`."""
    names = ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md")
    found = [d / n for d in (folder, *folder.parents) for n in names if (d / n).is_file()]
    return [f for f in found if f.parent != Path.home() / ".claude"]   # user memory: see CLAUDE_CONFIG_DIR


def process_env(config_dir: Path | None) -> dict[str, str]:
    """Environment of the agent process; the caller's environment is left untouched."""
    env = dict(os.environ)
    if config_dir is not None:
        env["CLAUDE_CONFIG_DIR"] = str(Path(config_dir).resolve())
    return env


def claude_environment(executable: str | list[str] = "claude", config_dir: Path | None = None) -> dict[str, Any]:
    """Version and plan of the Claude Code installation the agents use (never the account email)."""
    exe = _executable(executable)
    env: dict[str, Any] = {"separate_config_dir": config_dir is not None}
    penv = process_env(config_dir)

    def run(*args: str) -> str:
        return subprocess.run(exe + list(args), capture_output=True, text=True, encoding="utf-8",
                              timeout=60, env=penv).stdout

    try:
        env["claude_code_version"] = run("--version").strip()
        status = json.loads(run("auth", "status", "--json"))
        env.update({k: status.get(k) for k in ("subscriptionType", "authMethod", "apiProvider")})
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        env["error"] = f"{type(exc).__name__}: {exc}"
    return env


class ClaudeAgent:
    backend = "claude-code"

    def __init__(self, settings: ClaudeSettings, workdir: Path):
        self.settings = settings
        self.workdir = workdir
        self.run_id = workdir.resolve().parent.name

    def bind_run(self, run_id: str) -> None:
        """One sandbox per run, so nothing Claude Code keeps per folder is shared across runs."""
        self.run_id = run_id

    def turn(self, actor: str, week: int, system_prompt: str, briefing: str, mcp_url: str) -> TurnResult:
        base = (self.workdir / actor).resolve()   # claude runs in the sandbox: paths must be absolute
        base.mkdir(parents=True, exist_ok=True)
        sandbox = self.settings.sandbox_root / self.run_id / actor   # empty cwd
        sandbox.mkdir(parents=True, exist_ok=True)
        leaks = memory_files_above(sandbox)
        if leaks or any(sandbox.iterdir()):
            raise AgentError(f"{actor} week {week}: sandbox {sandbox} is not clean: {leaks or 'not empty'}")
        system_file, config_file = base / "system_prompt.md", base / "mcp.json"
        system_file.write_text(system_prompt, encoding="utf-8")
        config_file.write_text(json.dumps(mcp_config(mcp_url)), encoding="utf-8")
        cmd = build_command(self.settings, system_file, config_file)
        try:
            proc = subprocess.run(cmd, input=briefing, capture_output=True, text=True, encoding="utf-8",
                                  cwd=sandbox, timeout=self.settings.timeout_s,
                                  env=process_env(self.settings.config_dir))
        except subprocess.TimeoutExpired as exc:
            raise AgentError(f"{actor} week {week}: claude timed out after {exc.timeout}s") from exc
        (base / f"w{week:02d}_stream.jsonl").write_text(proc.stdout, encoding="utf-8")  # full transcript
        init, result, tool_uses = parse_stream(proc.stdout)
        result_text = (result or {}).get("result") or ""
        errored = proc.returncode != 0 or result is None or bool(result.get("is_error"))
        if errored and LIMIT_PATTERN.search(f"{result_text}\n{proc.stderr}"):
            raise UsageLimitReached(f"{actor} week {week}: {result_text or proc.stderr}".strip(), result_text)
        if result is None or (errored and result.get("subtype") != "error_max_turns"):
            raise AgentError(f"{actor} week {week}: exit {proc.returncode}, "
                             f"{result_text or proc.stderr.strip()[-2000:]}")
        meta = {
            "backend": self.backend,
            "model": init.get("model"),
            "claude_code_version": init.get("claude_code_version"),
            "permission_mode": init.get("permissionMode"),
            "tools": init.get("tools") if init else None,
            "mcp_servers": init.get("mcp_servers"),
            "skills": init.get("skills"),
            "plugins": init.get("plugins"),
            "agents": init.get("agents"),
            "memory_paths": init.get("memory_paths"),
            "subtype": result.get("subtype"),
            "num_turns": result.get("num_turns"),
            "duration_ms": result.get("duration_ms"),
            "total_cost_usd": result.get("total_cost_usd"),
            "permission_denials": result.get("permission_denials") or [],
            "tool_uses": tool_uses,
        }
        return TurnResult(result_text, result.get("usage") or {}, meta)
