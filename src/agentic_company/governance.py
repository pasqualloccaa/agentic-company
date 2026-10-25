"""Governance state: what is not money and therefore not in the ledger.

Board agenda and minutes, declared conflicts, reports received by the board,
announcements to staff, diaries and the log of every tool call attempt with its
gate outcome. Decisions that change the economy are queued here and reach the
engine at the next weekly step through GovernancePolicy.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Mapping

from .engine import Action
from .gates import ALLOW, GateContext, GateResult, normalise


@dataclass
class Attempt:
    seq: int
    week: int
    actor: str
    tool: str
    params: dict[str, Any]
    outcome: str
    check: str
    reasons: tuple[str, ...]
    item_id: int | None = None


@dataclass
class AgendaItem:
    item_id: int
    week: int
    action: Action
    reasons: tuple[str, ...]
    status: str = "open"            # open, approved, rejected
    decided_week: int | None = None
    motivation: str = ""


@dataclass
class Minute:
    week: int
    actor: str
    item_id: int
    decision: str
    motivation: str


@dataclass
class BoardReport:
    report_id: int
    week: int
    actor: str
    week_from: int
    week_to: int
    kpis: dict[str, float]
    narrative: str


@dataclass
class Announcement:
    announcement_id: int
    week: int
    actor: str
    text: str
    numbers: dict[str, float]
    week_from: int
    week_to: int


@dataclass
class Conflict:
    week: int
    actor: str
    subject: str
    item_id: int | None
    note: str


@dataclass
class DiaryEntry:
    week: int
    actor: str
    text: str


def to_engine(action: Action) -> Action:
    """Translate an executable tool call into the engine action that carries it out."""
    if action.kind == "set_price":
        return action
    if action.kind == "approve_spend":
        return Action("spend", dict(action.params), action.actor)
    raise ValueError(f"Not an engine action: {action.kind}")


class Governance:
    def __init__(self, rules: Mapping[str, Any]):
        self.rules = rules
        self.week = 0
        self.meeting_open = False
        self.last_meeting_week = 0
        self.attempts: list[Attempt] = []
        self.agenda: list[AgendaItem] = []
        self.minutes: list[Minute] = []
        self.reports: list[BoardReport] = []
        self.announcements: list[Announcement] = []
        self.conflicts: list[Conflict] = []
        self.diaries: list[DiaryEntry] = []
        self.price_ref: dict[str, float] = {}
        self.queued: list[Action] = []

    # ------------------------------------------------------------------ log
    def record(self, action: Action, result: GateResult, item_id: int | None = None) -> Attempt:
        attempt = Attempt(len(self.attempts) + 1, self.week, action.actor, action.kind, dict(action.params),
                          result.outcome, result.check, result.reasons, item_id)
        self.attempts.append(attempt)
        return attempt

    # ------------------------------------------------------------------ economy
    def queue(self, action: Action) -> None:
        self.queued.append(action)

    def take_queued(self) -> list[Action]:
        out, self.queued = self.queued, []
        return [to_engine(a) for a in out]

    # ------------------------------------------------------------------ board
    def open_item(self, action: Action, reasons: tuple[str, ...]) -> AgendaItem:
        item = AgendaItem(len(self.agenda) + 1, self.week, action, reasons)
        self.agenda.append(item)
        return item

    def item(self, item_id: int) -> AgendaItem:
        return self.agenda[item_id - 1]

    def open_meeting(self) -> None:
        self.meeting_open = True

    def close_meeting(self) -> None:
        self.meeting_open = False
        self.last_meeting_week = self.week

    def decide(self, item_id: int, decision: str, motivation: str, actor: str, sim) -> AgendaItem:
        item = self.item(item_id)
        item.status = "approved" if decision == "approve" else "rejected"
        item.decided_week, item.motivation = self.week, motivation
        self.minutes.append(Minute(self.week, actor, item_id, decision, motivation))
        if decision == "approve":
            if item.action.kind == "set_price":
                cid = item.action.params["customer"]
                self.price_ref[cid] = self._price_mult(sim)[cid] * (1 + item.action.params["pct"])
            self.queue(item.action)
        return item

    def declare_conflict(self, actor: str, subject: str, item_id: int | None, note: str) -> Conflict:
        conflict = Conflict(self.week, actor, subject, item_id, note)
        self.conflicts.append(conflict)
        return conflict

    # ------------------------------------------------------------------ communications
    def add_report(self, actor: str, week_from: int, week_to: int, kpis: dict, narrative: str) -> BoardReport:
        report = BoardReport(len(self.reports) + 1, self.week, actor, week_from, week_to, dict(kpis), narrative)
        self.reports.append(report)
        return report

    def add_announcement(self, actor: str, text: str, numbers: dict, week_from: int, week_to: int) -> Announcement:
        ann = Announcement(len(self.announcements) + 1, self.week, actor, text, dict(numbers), week_from, week_to)
        self.announcements.append(ann)
        return ann

    def write_diary(self, actor: str, text: str) -> DiaryEntry:
        entry = DiaryEntry(self.week, actor, text)
        self.diaries.append(entry)
        return entry

    # ------------------------------------------------------------------ gate view
    def _price_mult(self, sim) -> dict[str, float]:
        mult = dict(sim.price_mult)
        for a in self.queued:
            if a.kind == "set_price":
                mult[a.params["customer"]] *= 1 + a.params["pct"]
        return mult

    def context(self, sim) -> GateContext:
        spent: dict[str, float] = defaultdict(float)
        for a in self.attempts:
            if a.tool == "approve_spend" and a.outcome == ALLOW and a.week > self.last_meeting_week:
                spent[normalise(a.params["counterparty"])] += a.params["amount"]
        L = sim.ledger
        undrawn = 0.0 if sim.revolver_frozen else max(0.0, sim.revolver_limit - L.balance("revolver"))
        queued_spend = sum(a.params["amount"] for a in self.queued if a.kind == "approve_spend")
        return GateContext(
            week=self.week,
            rules=self.rules,
            customers=frozenset(sim.customers),
            meeting_open=self.meeting_open,
            price_mult=self._price_mult(sim),
            price_ref={cid: self.price_ref.get(cid, 1.0) for cid in sim.customers},
            spent_since_meeting=dict(spent),
            funds_available=L.balance("cash") + undrawn - queued_spend,
            open_items={i.item_id: i.action for i in self.agenda if i.status == "open"},
            conflicts=frozenset((c.actor, c.item_id) for c in self.conflicts if c.item_id is not None),
        )


@dataclass
class GovernancePolicy:
    """Engine policy that executes the decisions queued by the agents."""
    gov: Governance = field(repr=False)

    def decide(self, sim, week: int) -> list[Action]:
        return self.gov.take_queued()
