"""Stand-in for the `claude` executable in tests: prints stream-json like `claude -p`.

FAKE_CLAUDE_MODE: ok | limit | error | leaky | max_turns
FAKE_CLAUDE_LOG: file where argv, cwd and stdin are written as JSON
"""
import json
import os
import sys

mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
argv = sys.argv[1:]
prompt = sys.stdin.read()
if os.environ.get("FAKE_CLAUDE_LOG"):
    with open(os.environ["FAKE_CLAUDE_LOG"], "w", encoding="utf-8") as fh:
        json.dump({"argv": argv, "cwd": os.getcwd(), "stdin": prompt,
                   "config_dir": os.environ.get("CLAUDE_CONFIG_DIR")}, fh)

for flag in ("--system-prompt-file", "--mcp-config"):
    if not os.path.exists(argv[argv.index(flag) + 1]):  # resolved against cwd, like claude
        print(f"Error: file not found: {argv[argv.index(flag) + 1]}", file=sys.stderr)
        sys.exit(1)
tools = argv[argv.index("--allowedTools") + 1].split(",")
if mode == "leaky":
    tools = tools + ["Bash", "mcp__gmail__send"]
print(json.dumps({"type": "system", "subtype": "init", "model": "fake-model", "claude_code_version": "0.0.0",
                  "permissionMode": "default", "tools": tools, "skills": [], "plugins": [],
                  "mcp_servers": [{"name": "sim", "status": "connected"}], "memory_paths": None}))
print(json.dumps({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "name": "mcp__sim__get_kpis", "input": {}}]}}))
if mode == "limit":
    print(json.dumps({"type": "result", "subtype": "success", "is_error": True,
                      "result": "Claude AI usage limit reached|1761400000"}))
    sys.exit(1)
if mode == "error":
    print("boom", file=sys.stderr)
    sys.exit(2)
subtype = "error_max_turns" if mode == "max_turns" else "success"
print(json.dumps({"type": "result", "subtype": subtype, "is_error": mode == "max_turns",
                  "result": "" if mode == "max_turns" else "Ho controllato i conti.", "num_turns": 3,
                  "usage": {"input_tokens": 10, "output_tokens": 5}, "permission_denials": []}))
