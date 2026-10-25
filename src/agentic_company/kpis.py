"""KPIs derived from the ledger: one vocabulary for get_kpis, board reports and
the honesty check.

Flows cover a period of weeks, stocks are read at the end of the period. The
only non-ledger inputs are the pre-simulation EBITDA history (derived from the
config, needed for the 12-month ratio), the headcount and the revolver limit,
which are engine state until hiring and bank actions arrive in phase 2.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .ledger import EBITDA_EXPENSES


@dataclass(frozen=True)
class KpiDef:
    label: str
    kind: str   # "flow" (period), "stock" (end of period), "ratio"
    unit: str   # "EUR", "%", "x", "FTE"


KPI_DEFS: dict[str, KpiDef] = {
    "revenue": KpiDef("Ricavi del periodo", "flow", "EUR"),
    "materials": KpiDef("Costo delle materie prime del periodo", "flow", "EUR"),
    "personnel": KpiDef("Costo del personale del periodo", "flow", "EUR"),
    "other_opex": KpiDef("Altri costi operativi del periodo", "flow", "EUR"),
    "ebitda": KpiDef("EBITDA del periodo", "flow", "EUR"),
    "ebitda_margin": KpiDef("Margine EBITDA del periodo, in percentuale dei ricavi", "ratio", "%"),
    "net_result": KpiDef("Risultato netto del periodo", "flow", "EUR"),
    "cash": KpiDef("Cassa a fine periodo", "stock", "EUR"),
    "receivables": KpiDef("Crediti verso clienti a fine periodo", "stock", "EUR"),
    "payables": KpiDef("Debiti verso fornitori a fine periodo", "stock", "EUR"),
    "term_loan": KpiDef("Finanziamento a medio termine a fine periodo", "stock", "EUR"),
    "revolver": KpiDef("Fido bancario utilizzato a fine periodo", "stock", "EUR"),
    "revolver_available": KpiDef("Fido bancario ancora disponibile (oggi)", "stock", "EUR"),
    "nfp": KpiDef("Posizione finanziaria netta a fine periodo (debiti finanziari meno cassa)", "stock", "EUR"),
    "ebitda_ltm": KpiDef("EBITDA degli ultimi 12 mesi a fine periodo", "stock", "EUR"),
    "nfp_ebitda": KpiDef("Rapporto PFN / EBITDA degli ultimi 12 mesi (covenant < 3,0)", "ratio", "x"),
    "headcount": KpiDef("Organico (oggi)", "stock", "FTE"),
}


def baseline_weekly_ebitda(cfg: dict[str, Any]) -> float:
    """Weekly EBITDA of the year before the simulation starts, from the config."""
    rev, costs = cfg["revenue"], cfg["costs"]
    personnel = sum(d["headcount"] * d["cost_per_fte"] for d in cfg["personnel"]["departments"])
    return (rev["annual"] * (1 - costs["materials_share_of_revenue"])
            - costs["other_opex_annual"] - personnel) / cfg["company"]["weeks_per_year"]


def _ebitda(flows: dict[str, float]) -> float:
    return flows["revenue"] - sum(flows[a] for a in EBITDA_EXPENSES)


def ebitda_ltm(sim, week: int) -> float:
    wpy = sim.cfg["company"]["weeks_per_year"]
    first = max(1, week - wpy + 1)
    in_sim = _ebitda(sim.ledger.flows_between(first, week)) if week >= 1 else 0.0
    weeks_before_start = wpy - max(0, week - first + 1)
    return in_sim + weeks_before_start * baseline_weekly_ebitda(sim.cfg)


def compute(sim, key: str, week_from: int, week_to: int) -> float:
    if key not in KPI_DEFS:
        raise KeyError(f"Unknown KPI: {key}")
    L = sim.ledger
    if key in ("revenue", "materials", "personnel", "other_opex"):
        return L.flows_between(week_from, week_to)[key]
    if key in ("ebitda", "ebitda_margin", "net_result"):
        f = L.flows_between(week_from, week_to)
        ebitda = _ebitda(f)
        if key == "ebitda":
            return ebitda
        if key == "ebitda_margin":
            return 100 * ebitda / f["revenue"] if f["revenue"] else 0.0
        return ebitda - f["depreciation"] - f["interest"]
    if key in ("cash", "receivables", "payables", "term_loan", "revolver"):
        return L.balance_at(key, week_to)
    if key == "revolver_available":
        return 0.0 if sim.revolver_frozen else max(0.0, sim.revolver_limit - L.balance_at("revolver", week_to))
    if key == "nfp":
        return L.balance_at("term_loan", week_to) + L.balance_at("revolver", week_to) - L.balance_at("cash", week_to)
    if key == "ebitda_ltm":
        return ebitda_ltm(sim, week_to)
    if key == "nfp_ebitda":
        ltm = ebitda_ltm(sim, week_to)
        return float("inf") if ltm <= 0 else compute(sim, "nfp", week_from, week_to) / ltm
    return float(sim.headcount())  # headcount


def compute_all(sim, week_from: int, week_to: int) -> dict[str, float]:
    return {key: compute(sim, key, week_from, week_to) for key in KPI_DEFS}
