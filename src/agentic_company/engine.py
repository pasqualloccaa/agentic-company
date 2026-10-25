"""Deterministic economic engine (phase 0: no LLM).

One tick = one week. Given the same config, scenario, policy and seed, a run
produces exactly the same ledger. Agents will later act only through actions;
in phase 0 a fixed policy stands in for them.
"""
from __future__ import annotations

import random
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

from .ledger import EBITDA_EXPENSES, Ledger, Line

ROOT = Path(__file__).resolve().parents[2]


def load_yaml(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# --------------------------------------------------------------------------- actions

@dataclass(frozen=True)
class Action:
    kind: str
    params: dict[str, Any] = field(default_factory=dict)
    actor: str = "policy"


class Policy(Protocol):
    def decide(self, sim: "Simulation", week: int) -> list[Action]: ...


class NoOpPolicy:
    """Baseline: management takes no decisions at all."""

    def decide(self, sim: "Simulation", week: int) -> list[Action]:
        return []


# --------------------------------------------------------------------------- engine

@dataclass
class Event:
    week: int
    type: str
    note: str
    data: dict[str, Any] = field(default_factory=dict)


class Simulation:
    MIN_CASH = 250_000      # treasury draws on the revolver below this level
    MAX_CASH = 2_000_000    # and repays it above this level

    def __init__(self, company: dict, scenario: dict, policy: Policy | None = None, seed: int = 42):
        self.cfg = company
        self.scenario = scenario
        self.policy = policy or NoOpPolicy()
        self.seed = seed
        self.rng = random.Random(seed)
        self.ledger = Ledger()
        self.events: list[Event] = []
        self.snapshots: list[dict[str, Any]] = []
        self.week = 0

        w = company["company"]["weeks_per_year"]
        self.wpy = w
        rev = company["revenue"]
        self.customers = {c["id"]: c for c in rev["customers"]}
        self.base_weekly_revenue = {c["id"]: rev["annual"] * c["share"] / w for c in rev["customers"]}
        self.volume_mult = {cid: 1.0 for cid in self.customers}
        self.price_mult = {cid: 1.0 for cid in self.customers}
        self.steel_mult = 1.0

        costs = company["costs"]
        self.materials_weekly = rev["annual"] * costs["materials_share_of_revenue"] / w
        self.opex_weekly = costs["other_opex_annual"] / w
        self.depr_weekly = costs["depreciation_annual"] / w

        self.departments = {d["id"]: dict(d) for d in company["personnel"]["departments"]}

        fin = company["financing"]
        self.revolver_limit = fin["revolver_limit"]
        self.revolver_frozen = False

        # cash schedules: week -> amount to collect / pay
        self.collections: dict[int, float] = defaultdict(float)
        self.supplier_payments: dict[int, float] = defaultdict(float)

        baseline_ebitda = (rev["annual"] * (1 - costs["materials_share_of_revenue"])
                           - costs["other_opex_annual"] - self.annual_personnel_cost()) / w
        self.ebitda_ltm = deque([baseline_ebitda] * w, maxlen=w)

        self._open_books()

    # ------------------------------------------------------------------ helpers
    def annual_personnel_cost(self) -> float:
        return sum(d["headcount"] * d["cost_per_fte"] for d in self.departments.values())

    def headcount(self) -> int:
        return sum(d["headcount"] for d in self.departments.values())

    def nfp(self) -> float:
        """Net financial position: debt minus cash (positive = net debt)."""
        b = self.ledger.balance
        return b("term_loan") + b("revolver") - b("cash")

    def nfp_ebitda(self) -> float:
        ltm = sum(self.ebitda_ltm)
        return float("inf") if ltm <= 0 else self.nfp() / ltm

    def log(self, type_: str, note: str, **data: Any) -> None:
        self.events.append(Event(self.week, type_, note, data))

    # ------------------------------------------------------------------ opening
    def _open_books(self) -> None:
        wc = self.cfg["working_capital"]
        fin = self.cfg["financing"]
        base_rev = sum(self.base_weekly_revenue.values())
        base_pay = self.materials_weekly + self.opex_weekly

        receivables = base_rev * wc["receivable_weeks"]
        payables = base_pay * wc["payable_weeks"]
        for k in range(1, wc["receivable_weeks"] + 1):
            self.collections[k] += base_rev
        for k in range(1, wc["payable_weeks"] + 1):
            self.supplier_payments[k] += base_pay

        assets = fin["opening_cash"] + receivables + self.cfg["balance_sheet"]["fixed_assets_net"]
        liabilities = payables + fin["term_loan"] + fin["revolver_drawn"]
        self.ledger.post(0, "Situazione patrimoniale di apertura", [
            Line("cash", debit=round(fin["opening_cash"], 2)),
            Line("receivables", debit=round(receivables, 2)),
            Line("fixed_assets", debit=round(self.cfg["balance_sheet"]["fixed_assets_net"], 2)),
            Line("payables", credit=round(payables, 2)),
            Line("term_loan", credit=round(fin["term_loan"], 2)),
            Line("revolver", credit=round(fin["revolver_drawn"], 2)),
            Line("equity", credit=round(assets - liabilities, 2)),
        ], origin="opening")

    # ------------------------------------------------------------------ shocks
    def _apply_shocks(self) -> None:
        for shock in self.scenario.get("shocks", []):
            if shock["week"] != self.week:
                continue
            kind = shock["type"]
            if kind == "steel_price":
                self.steel_mult *= 1 + shock["change"]
            elif kind == "customer_volume":
                self.volume_mult[shock["customer"]] *= 1 + shock["change"]
            elif kind == "bank_review":
                ratio = self.nfp_ebitda()
                ok = ratio < self.cfg["financing"]["covenant_max_nfp_ebitda"]
                if not ok:
                    self.revolver_frozen = True
                    self.revolver_limit = self.ledger.balance("revolver")
                self.log("bank_review", shock["note"], nfp_ebitda=round(ratio, 3), covenant_ok=ok)
                continue
            elif kind != "event":
                raise ValueError(f"Unknown shock type: {kind}")
            self.log("shock", shock["note"], **{k: v for k, v in shock.items() if k not in ("week", "note")})

    # ------------------------------------------------------------------ actions
    def _apply_action(self, action: Action) -> None:
        if action.kind == "set_price":
            cid, pct = action.params["customer"], action.params["pct"]
            self.price_mult[cid] *= 1 + pct
            self.log("action", f"Prezzo {cid} {pct:+.1%}", actor=action.actor, **action.params)
        else:
            raise ValueError(f"Unsupported action in phase 0: {action.kind}")

    # ------------------------------------------------------------------ tick
    def step(self) -> dict[str, Any]:
        self.week += 1
        w, L = self.week, self.ledger
        wc = self.cfg["working_capital"]
        fin = self.cfg["financing"]

        self._apply_shocks()
        for action in self.policy.decide(self, w):
            self._apply_action(action)

        # sales: invoices now, cash after the receivable period
        revenue, volume = 0.0, 0.0
        for cid in self.customers:
            noise = 1 + self.rng.gauss(0, self.cfg["revenue"]["weekly_noise_sd"])
            units = self.base_weekly_revenue[cid] * self.volume_mult[cid] * noise
            volume += units
            revenue += units * self.price_mult[cid]
        revenue = round(revenue, 2)
        L.transfer(w, "Fatture di vendita", "receivables", "revenue", revenue)
        self.collections[w + wc["receivable_weeks"]] += revenue

        # purchases: materials follow volume and steel price; paid after the payable period
        volume_ratio = volume / sum(self.base_weekly_revenue.values())
        steel_share = self.cfg["costs"]["steel_share_of_materials"]
        materials = round(self.materials_weekly * volume_ratio * (1 + steel_share * (self.steel_mult - 1)), 2)
        opex = round(self.opex_weekly, 2)
        L.transfer(w, "Acquisti materie prime", "materials", "payables", materials)
        L.transfer(w, "Altri costi operativi", "other_opex", "payables", opex)
        self.supplier_payments[w + wc["payable_weeks"]] += materials + opex

        # personnel accrues weekly, paid every N weeks
        L.transfer(w, "Competenze del personale", "personnel", "payroll_payable",
                   self.annual_personnel_cost() / self.wpy)
        if w % self.cfg["personnel"]["payroll_every_weeks"] == 0:
            L.transfer(w, "Pagamento stipendi", "payroll_payable", "cash", L.balance("payroll_payable"))

        L.transfer(w, "Ammortamenti", "depreciation", "fixed_assets", self.depr_weekly)

        interest = (L.balance("term_loan") * fin["term_loan_rate"]
                    + L.balance("revolver") * fin["revolver_rate"]) / self.wpy
        L.transfer(w, "Interessi passivi", "interest", "cash", interest)

        # cash movements due this week
        L.transfer(w, "Incassi da clienti", "cash", "receivables", self.collections.pop(w, 0.0))
        L.transfer(w, "Pagamenti a fornitori", "payables", "cash", self.supplier_payments.pop(w, 0.0))
        if w % 13 == 0:
            L.transfer(w, "Rata finanziamento", "term_loan", "cash",
                       min(fin["term_loan_repayment"], L.balance("term_loan")))

        self._treasury()

        flows = L.flows(w)
        ebitda = flows["revenue"] - sum(flows[a] for a in EBITDA_EXPENSES)
        self.ebitda_ltm.append(ebitda)
        snap = self._snapshot(flows, ebitda)
        self.snapshots.append(snap)
        return snap

    def _treasury(self) -> None:
        """Automatic treasury: keep cash between MIN_CASH and MAX_CASH using the revolver."""
        L, w = self.ledger, self.week
        cash, drawn = L.balance("cash"), L.balance("revolver")
        if cash < self.MIN_CASH:
            need = self.MIN_CASH - cash
            available = 0.0 if self.revolver_frozen else max(0.0, self.revolver_limit - drawn)
            draw = min(need, available)
            L.transfer(w, "Utilizzo fido", "cash", "revolver", draw)
            if L.balance("cash") < 0:
                self.log("liquidity_crisis", "Cassa negativa: fido esaurito o bloccato",
                         cash=round(L.balance("cash"), 2))
        elif cash > self.MAX_CASH and drawn > 0:
            L.transfer(w, "Rientro fido", "revolver", "cash", min(cash - self.MAX_CASH, drawn))

    def _snapshot(self, flows: dict[str, float], ebitda: float) -> dict[str, Any]:
        b = self.ledger.balance
        ebit = ebitda - flows["depreciation"]
        ratio = self.nfp_ebitda()
        return {
            "week": self.week,
            "revenue": round(flows["revenue"], 2),
            "materials": round(flows["materials"], 2),
            "personnel": round(flows["personnel"], 2),
            "other_opex": round(flows["other_opex"], 2),
            "ebitda": round(ebitda, 2),
            "ebit": round(ebit, 2),
            "net_result": round(ebit - flows["interest"], 2),
            "cash": round(b("cash"), 2),
            "receivables": round(b("receivables"), 2),
            "payables": round(b("payables"), 2),
            "term_loan": round(b("term_loan"), 2),
            "revolver": round(b("revolver"), 2),
            "revolver_limit": round(self.revolver_limit, 2),
            "nfp": round(self.nfp(), 2),
            "ebitda_ltm": round(sum(self.ebitda_ltm), 2),
            "nfp_ebitda": round(ratio, 3),
            "covenant_ok": ratio < self.cfg["financing"]["covenant_max_nfp_ebitda"],
            "headcount": self.headcount(),
            "steel_mult": round(self.steel_mult, 4),
        }

    def run(self, weeks: int | None = None) -> list[dict[str, Any]]:
        for _ in range(weeks or self.scenario["weeks"]):
            self.step()
        return self.snapshots


def build(seed: int = 42, policy: Policy | None = None,
          company_path: str | Path | None = None,
          scenario_path: str | Path | None = None) -> Simulation:
    company = load_yaml(company_path or ROOT / "config" / "company.yaml")
    scenario = load_yaml(scenario_path or ROOT / "config" / "scenario_base.yaml")
    return Simulation(company, scenario, policy, seed)
