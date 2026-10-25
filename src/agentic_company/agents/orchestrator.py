"""The run loop: engine -> Uffici -> CdA (every 4 weeks, after the close) -> outputs.

All outputs are rewritten after every complete tick. If an agent hits the
account usage limit the run pauses; it can wait and retry, or stop and be
resumed later. Either way the state is rebuilt the same way: a fresh simulation
with the same seed, replaying actions.csv and turns.jsonl up to the last
complete tick, and checking that every gate outcome comes out identical.
The interrupted tick is discarded and played again.
"""
from __future__ import annotations

import csv
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .. import honesty
from ..engine import ROOT, Simulation, load_config
from ..governance import Governance, GovernancePolicy
from ..mcp_server import SimulationHost
from ..run import write_outputs
from ..tools import Toolbox
from .claude_cli import TurnResult, UsageLimitReached, isolation_problems
from .memory import TOKEN_BUDGET, TurnRecord, cda_briefing, estimate_tokens, uffici_briefing
from .personas import load_personas, system_prompt

ACTORS = ("uffici", "cda")
ACTION_FIELDS = ["seq", "week", "actor", "tool", "params", "outcome", "check", "reasons", "item_id"]


class Agent(Protocol):
    backend: str

    def turn(self, actor: str, week: int, system_prompt: str, briefing: str, mcp_url: str) -> TurnResult: ...


class RunPaused(Exception):
    pass


class IsolationError(RuntimeError):
    pass


class ReplayError(RuntimeError):
    pass


@dataclass
class RunConfig:
    out: Path
    seed: int = 42
    company: Path = ROOT / "config" / "company_mvp.yaml"
    scenario: Path = ROOT / "config" / "scenario_mvp.yaml"
    governance: Path = ROOT / "config" / "governance.yaml"
    personas: Path = ROOT / "personas"
    weeks: int | None = None
    wait_on_limit: bool = False
    limit_wait_s: float = 900.0
    max_limit_waits: int = 12
    environment: dict[str, Any] = field(default_factory=dict)  # Claude Code version, plan, model


class Orchestrator:
    def __init__(self, config: RunConfig, agent: Agent):
        self.cfg, self.agent = config, agent
        self.out = Path(config.out)
        self.company = load_config(config.company)
        self.scenario = load_config(config.scenario)
        self.rules = load_config(config.governance)
        self.weeks = config.weeks or self.scenario["weeks"]
        personas = load_personas(config.personas)
        missing = [a for a in ACTORS if a not in personas]
        if missing:
            raise ValueError(f"{config.personas}: no persona for {missing}")
        self.system = {a: system_prompt(personas[a], self.rules, self.company, self.weeks) for a in ACTORS}
        self.host: SimulationHost | None = None
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.isolation: list[dict[str, Any]] = []
        self.run_id = ""
        self._fresh()

    # ------------------------------------------------------------------ state
    def _fresh(self) -> None:
        self.gov = Governance(self.rules)
        self.sim = Simulation(self.company, self.scenario, GovernancePolicy(self.gov), self.cfg.seed)
        self.tb = Toolbox(self.sim, self.gov)
        self.turns: list[TurnRecord] = []
        if self.host is not None:
            self.host.set_toolbox(self.tb)

    def _meeting_week(self, week: int) -> bool:
        return week % self.rules["board"]["meeting_every_weeks"] == 0

    def last_complete_week(self) -> int:
        path = self.out / "run.json"
        if not path.exists():
            return 0
        return json.loads(path.read_text(encoding="utf-8")).get("last_complete_week", 0)

    def restore(self) -> int:
        """Rebuild the state of the last complete tick from the logs on disk."""
        last = self.last_complete_week()
        run = json.loads((self.out / "run.json").read_text(encoding="utf-8")) if last else {}
        if last and (run.get("seed"), run.get("scenario")) != (self.cfg.seed, self.scenario["name"]):
            raise ReplayError(f"{self.out} belongs to another run: {run.get('seed')}/{run.get('scenario')}")
        self._fresh()
        rows = _read_actions(self.out / "actions.csv") if last else []
        turns = [TurnRecord(**t) for t in _read_jsonl(self.out / "turns.jsonl")] if last else []
        for week in range(1, last + 1):
            self.tb.advance()
            self._replay(rows, week, "uffici")
            if self._meeting_week(week):
                self.gov.open_meeting()
                self._replay(rows, week, "cda")
                self.gov.close_meeting()
        self.turns = [t for t in turns if t.week <= last]
        return last

    def _replay(self, rows: list[dict[str, str]], week: int, actor: str) -> None:
        for row in rows:
            if int(row["week"]) != week or row["actor"] != actor:
                continue
            self.tb.call(actor, row["tool"], json.loads(row["params"]))
            got = self.gov.attempts[-1]
            if (got.seq, got.outcome, str(got.item_id or "")) != (int(row["seq"]), row["outcome"], row["item_id"]):
                raise ReplayError(f"replay diverged at seq {row['seq']}: {row['outcome']} != {got.outcome}")

    # ------------------------------------------------------------------ run
    def run(self, resume: bool = False) -> dict[str, Any]:
        self.out.mkdir(parents=True, exist_ok=True)
        if resume:
            start = self.restore()
            self.run_id = json.loads((self.out / "run.json").read_text(encoding="utf-8"))["run_id"]
        else:
            (self.out / "run.json").unlink(missing_ok=True)
            start = 0
            self._fresh()
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            self.run_id = f"{self.scenario['name']}_s{self.cfg.seed}_{stamp}_{uuid.uuid4().hex[:6]}"
        if hasattr(self.agent, "bind_run"):
            self.agent.bind_run(self.run_id)  # type: ignore[attr-defined]
        host = self.host = SimulationHost(self.tb, ACTORS)
        host.start()
        waits = 0
        try:
            week = start
            while week < self.weeks:
                try:
                    self._tick(week + 1)
                    week += 1
                except UsageLimitReached as exc:
                    if not self.cfg.wait_on_limit or waits >= self.cfg.max_limit_waits:
                        self._write_run_json("paused", reason=str(exc))
                        raise RunPaused(f"Limite d'uso raggiunto alla settimana {week + 1}: {exc}. "
                                        f"Riprendi con --resume.") from exc
                    waits += 1
                    time.sleep(self.cfg.limit_wait_s)
                    week = self.restore()
            self._write_run_json("completed")
        finally:
            host.stop()
        return {"status": "completed", "weeks": self.weeks}

    def _tick(self, week: int) -> None:
        self.tb.advance()
        self._turn("uffici")
        if self._meeting_week(week):
            self.gov.open_meeting()
            self._turn("cda")
            self.gov.close_meeting()
        self._save(week)

    def _turn(self, actor: str) -> None:
        assert self.host is not None
        w, system = self.gov.week, self.system[actor]
        budget = TOKEN_BUDGET - estimate_tokens(system)
        if actor == "uffici":
            briefing = uffici_briefing(self.sim, self.gov, self.turns, budget, self.tb.next_meeting_week())
        else:
            briefing = cda_briefing(self.sim, self.gov, self.turns, budget)
        prompts = self.out / "prompts"
        prompts.mkdir(exist_ok=True)
        (prompts / f"system_{actor}.md").write_text(system, encoding="utf-8")
        (prompts / f"w{w:02d}_{actor}.md").write_text(briefing, encoding="utf-8")
        first = len(self.gov.attempts) + 1
        result = self.agent.turn(actor, w, system, briefing, self.host.url(actor))
        if result.meta.get("backend") != "dry-run":
            problems = isolation_problems(result.meta)
            self.isolation.append({"week": w, "actor": actor, "problems": problems})
            if problems:
                self._write_run_json("aborted", reason=f"isolation: {problems}")
                raise IsolationError(f"{actor} week {w}: {problems}")
        self.turns.append(TurnRecord(w, actor, result.text, first, len(self.gov.attempts),
                                     {**result.usage, "meta": result.meta}))

    # ------------------------------------------------------------------ outputs
    def _save(self, week: int) -> None:
        out = self.out
        write_outputs(self.sim, out, run_info=False)
        with open(out / "actions.csv", "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=ACTION_FIELDS)
            writer.writeheader()
            for a in self.gov.attempts:
                writer.writerow({**asdict(a), "params": json.dumps(a.params, ensure_ascii=False),
                                 "reasons": " | ".join(a.reasons), "item_id": a.item_id or ""})
        with open(out / "honesty.csv", "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(honesty.Claim.__dataclass_fields__))
            writer.writeheader()
            writer.writerows(asdict(c) for c in self.gov.claims)
        with open(out / "turns.jsonl", "w", encoding="utf-8") as fh:
            for t in self.turns:
                fh.write(json.dumps(asdict(t), ensure_ascii=False) + "\n")
        with open(out / "agenda.csv", "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["item_id", "week", "actor", "tool", "params", "reasons", "status",
                             "decided_week", "motivation"])
            for i in self.gov.agenda:
                writer.writerow([i.item_id, i.week, i.action.actor, i.action.kind,
                                 json.dumps(i.action.params, ensure_ascii=False), " | ".join(i.reasons),
                                 i.status, i.decided_week or "", i.motivation])
        (out / "communications.json").write_text(json.dumps({
            "board_reports": [asdict(r) for r in self.gov.reports],
            "staff_announcements": [asdict(a) for a in self.gov.announcements],
            "conflicts": [asdict(c) for c in self.gov.conflicts],
            "minutes": [asdict(m) for m in self.gov.minutes],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        diaries = out / "diaries"
        diaries.mkdir(exist_ok=True)
        for actor in ACTORS:
            entries = [f"## Settimana {d.week}\n{d.text}\n" for d in self.gov.diaries if d.actor == actor]
            (diaries / f"{actor}.md").write_text("\n".join(entries), encoding="utf-8")
        self._write_run_json("running", last_complete_week=week)

    def _write_run_json(self, status: str, reason: str = "", last_complete_week: int | None = None) -> None:
        path = self.out / "run.json"
        previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        data = {
            "run_id": self.run_id,
            "seed": self.cfg.seed,
            "scenario": self.scenario["name"],
            "company": Path(self.cfg.company).name,
            "weeks": self.weeks,
            "backend": self.agent.backend,
            "environment": self.cfg.environment,
            "models_used": sorted({t.usage["meta"]["model"] for t in self.turns
                                   if t.usage.get("meta", {}).get("model")}),
            "status": status,
            "reason": reason,
            "last_complete_week": (last_complete_week if last_complete_week is not None
                                   else previous.get("last_complete_week", 0)),
            "started_at": previous.get("started_at", self.started_at),
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "entries": len(self.sim.ledger.entries),
            "trial_balance": self.sim.ledger.trial_balance(),
            "isolation_checks": previous.get("isolation_checks", []) + self.isolation,
        }
        self.isolation = []
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_actions(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
