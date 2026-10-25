"""Full MVP runs with the scripted agents (MCP over local HTTP, no Claude)."""
import csv
import json
from pathlib import Path

import pytest

from agentic_company.agents.claude_cli import TurnResult, UsageLimitReached, allowed_tools
from agentic_company.agents.dry_run import DryRunAgent
from agentic_company.agents.memory import TOKEN_BUDGET, estimate_tokens
from agentic_company.agents.orchestrator import IsolationError, Orchestrator, RunConfig, RunPaused

FIXTURES = Path(__file__).parent / "fixtures" / "personas"
OUTPUTS = ("ticks.csv", "ledger.csv", "events.json", "actions.csv", "honesty.csv", "agenda.csv",
           "communications.json", "turns.jsonl", "diaries/uffici.md", "diaries/cda.md")


def orchestrator(out, agent, **kw):
    return Orchestrator(RunConfig(out=out, seed=42, personas=FIXTURES, **kw), agent)


def rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    out = tmp_path_factory.mktemp("reference")
    orchestrator(out, DryRunAgent()).run()
    return out


class FlakyAgent:
    """Hits the usage limit once, in the middle of a turn (after some tool calls)."""
    backend = "dry-run"

    def __init__(self, week, actor):
        self.inner, self.at, self.fired = DryRunAgent(), (week, actor), False

    def turn(self, actor, week, system_prompt, briefing, mcp_url):
        result = self.inner.turn(actor, week, system_prompt, briefing, mcp_url)
        if (week, actor) == self.at and not self.fired:
            self.fired = True
            raise UsageLimitReached("usage limit reached", "")
        return result


def test_dry_run_completes_eight_weeks_with_every_output(reference):
    for name in OUTPUTS:
        assert (reference / name).exists(), name
    run = json.loads((reference / "run.json").read_text(encoding="utf-8"))
    assert run["status"] == "completed" and run["last_complete_week"] == 8
    assert run["trial_balance"] == 0 and run["backend"] == "dry-run"
    assert len(rows(reference / "ticks.csv")) == 8


def test_tick_order_and_board_meetings(reference):
    turns = [json.loads(l) for l in (reference / "turns.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(t["week"], t["actor"]) for t in turns] == \
           [(1, "uffici"), (2, "uffici"), (3, "uffici"), (4, "uffici"), (4, "cda"),
            (5, "uffici"), (6, "uffici"), (7, "uffici"), (8, "uffici"), (8, "cda")]


def test_actions_log_has_every_gate_outcome(reference):
    actions = rows(reference / "actions.csv")
    outcomes = {(a["actor"], a["tool"], a["outcome"]) for a in actions}
    assert ("cda", "read_ledger", "block") in outcomes
    assert ("uffici", "approve_spend", "needs_approval") in outcomes
    assert ("uffici", "set_price", "allow") in outcomes
    assert ("cda", "deliberate", "allow") in outcomes
    assert [int(a["seq"]) for a in actions] == list(range(1, len(actions) + 1))


def test_board_decisions_reach_the_ledger(reference):
    agenda = {r["item_id"]: r for r in rows(reference / "agenda.csv")}
    assert agenda["1"]["status"] == "approved" and agenda["2"]["status"] == "rejected"
    ledger = rows(reference / "ledger.csv")
    assert any(r["memo"] == "Investimento: Revisione della pressa 3" and r["week"] == "5" for r in ledger)
    assert not any("trasferta" in r["memo"] for r in ledger)


def test_honesty_flags_the_scripted_lie(reference):
    claims = rows(reference / "honesty.csv")
    lie = [c for c in claims if c["outcome"] == "false"]
    assert [(c["week"], c["kpi"]) for c in lie] == [("4", "revenue")]
    assert any(c["source"] == "staff_announcement" and c["outcome"] == "true" for c in claims)
    assert sum(c["outcome"] == "true" for c in claims) >= 7


def test_prompts_are_saved_and_within_budget(reference):
    prompts = reference / "prompts"
    system = {a: (prompts / f"system_{a}.md").read_text(encoding="utf-8") for a in ("uffici", "cda")}
    briefings = sorted(prompts.glob("w*_*.md"))
    assert len(briefings) == 10
    for path in briefings:
        actor = path.stem.split("_")[1]
        assert estimate_tokens(system[actor]) + estimate_tokens(path.read_text(encoding="utf-8")) <= TOKEN_BUDGET
    cda4 = (prompts / "w04_cda.md").read_text(encoding="utf-8")
    assert "Report n. 1" in cda4 and "Punto 1" in cda4


def test_diaries_are_written(reference):
    assert "Settimana 8: turno di prova." in (reference / "diaries" / "uffici.md").read_text(encoding="utf-8")


def _same_outputs(a, b):
    for name in OUTPUTS:
        assert (a / name).read_text(encoding="utf-8") == (b / name).read_text(encoding="utf-8"), name


class BindingAgent(DryRunAgent):
    def bind_run(self, run_id):
        self.run_id = run_id


def test_paused_run_resumes_identical_to_uninterrupted(tmp_path, reference):
    with pytest.raises(RunPaused):
        orchestrator(tmp_path, FlakyAgent(4, "cda")).run()
    paused = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert paused["status"] == "paused" and paused["last_complete_week"] == 3
    agent = BindingAgent()
    orchestrator(tmp_path, agent).run(resume=True)
    _same_outputs(tmp_path, reference)
    assert agent.run_id == paused["run_id"]   # same run, same sandbox


def test_every_run_gets_its_own_id(tmp_path):
    ids = []
    for k in range(2):
        agent = BindingAgent()
        orchestrator(tmp_path / str(k), agent, weeks=1).run()
        run = json.loads((tmp_path / str(k) / "run.json").read_text(encoding="utf-8"))
        assert run["run_id"] == agent.run_id and run["run_id"].startswith("mvp_s42_")
        ids.append(agent.run_id)
    assert ids[0] != ids[1]


def test_waiting_on_limit_replays_in_process(tmp_path, reference):
    orchestrator(tmp_path, FlakyAgent(6, "uffici"), wait_on_limit=True, limit_wait_s=0).run()
    _same_outputs(tmp_path, reference)


def test_resume_refuses_another_run(tmp_path):
    orchestrator(tmp_path, DryRunAgent(), weeks=2).run()
    other = Orchestrator(RunConfig(out=tmp_path, seed=7, personas=FIXTURES), DryRunAgent())
    with pytest.raises(Exception, match="another run"):
        other.run(resume=True)


class CleanClaudeAgent:
    backend = "claude-code"

    def turn(self, actor, week, system_prompt, briefing, mcp_url):
        return TurnResult("ok", {}, {"backend": "claude-code", "model": "fake-model", "tools": allowed_tools(),
                                     "mcp_servers": [{"name": "sim", "status": "connected"}]})


def test_run_json_records_the_models_actually_used(tmp_path):
    env = {"claude_code_version": "9.9.9", "subscriptionType": "max", "model_requested": "fake-model"}
    orchestrator(tmp_path, CleanClaudeAgent(), weeks=1, environment=env).run()
    run = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert run["models_used"] == ["fake-model"]
    assert run["environment"] == env
    assert run["isolation_checks"] == [{"week": 1, "actor": "uffici", "problems": []}]


def test_real_runs_require_an_explicit_model():
    from agentic_company.agents.__main__ import main
    with pytest.raises(SystemExit):
        main(["--seed", "1", "--weeks", "1"])


class LeakyAgent:
    backend = "claude-code"

    def turn(self, actor, week, system_prompt, briefing, mcp_url):
        return TurnResult("ok", {}, {"backend": "claude-code", "tools": allowed_tools() + ["Bash"],
                                     "mcp_servers": [{"name": "sim", "status": "connected"}]})


def test_isolation_breach_aborts_the_run(tmp_path):
    with pytest.raises(IsolationError):
        orchestrator(tmp_path, LeakyAgent()).run()
    run = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert run["status"] == "aborted" and "Bash" in run["reason"]
