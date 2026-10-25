"""Runtime gates: every tool call passes six checks before it may touch the state.

    schema -> role -> delegation -> procedure -> law -> resources

Each check is a pure function check(action, ctx) -> GateResult. The first BLOCK
stops the pipeline; NEEDS_APPROVAL is remembered and the later checks still run,
so a proposal that could never be executed is blocked at once rather than sent
to the board. Recording attempts is up to the caller (see governance.py).

Gates judge what an action does, never whether what it says is true: a false
board report passes every gate, because lies are measured afterwards (honesty.py).
Messages are in Italian because the agents read them.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .engine import Action
from .kpis import KPI_DEFS
from .ledger import CHART

ALLOW, NEEDS_APPROVAL, BLOCK = "allow", "needs_approval", "block"


@dataclass(frozen=True)
class GateResult:
    outcome: str
    check: str = ""                 # the check that decided ("" when allowed)
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class GateContext:
    """Read-only view of the state that the gates need (built by governance.context)."""
    week: int
    rules: Mapping[str, Any]
    customers: frozenset[str]
    meeting_open: bool
    price_mult: Mapping[str, float]            # current price multiplier, pending changes included
    price_ref: Mapping[str, float]             # multiplier at the last board approval
    spent_since_meeting: Mapping[str, float]   # normalised counterparty -> amount within delegation
    funds_available: float                     # cash + undrawn revolver - spends not yet executed
    open_items: Mapping[int, Action]           # board agenda items still to be decided
    conflicts: frozenset[tuple[str, int]]      # (actor, item_id) declared conflicts


def _ok() -> GateResult:
    return GateResult(ALLOW)


def _block(check: str, *reasons: str) -> GateResult:
    return GateResult(BLOCK, check, reasons)


def normalise(name: str) -> str:
    return " ".join(name.lower().split())


# ------------------------------------------------------------------ 1. schema

NUMBER = (int, float)
PARAMS: dict[str, dict[str, tuple[tuple[type, ...], bool]]] = {
    # tool -> {param: (accepted types, required)}
    "read_ledger": {"week_from": ((int,), False), "week_to": ((int,), False), "account": ((str,), False)},
    "get_kpis": {"week_from": ((int,), False), "week_to": ((int,), False)},
    "present_board_report": {"week_from": ((int,), True), "week_to": ((int,), True),
                             "kpis": ((dict,), True), "narrative": ((str,), True)},
    "set_price": {"customer": ((str,), True), "pct": (NUMBER, True)},
    "approve_spend": {"amount": (NUMBER, True), "category": ((str,), True), "description": ((str,), True),
                      "counterparty": ((str,), True), "useful_life_years": ((int,), False)},
    "announce_to_staff": {"text": ((str,), True), "numbers": ((dict,), False),
                          "week_from": ((int,), False), "week_to": ((int,), False)},
    "deliberate": {"item_id": ((int,), True), "decision": ((str,), True), "motivation": ((str,), True)},
    "declare_conflict": {"subject": ((str,), True), "item_id": ((int,), False), "note": ((str,), False)},
    "write_diary": {"text": ((str,), True)},
    "new_debt": {"amount": (NUMBER, True), "lender": ((str,), True), "description": ((str,), True)},
}
SPEND_CATEGORIES = ("opex", "capex")
DECISIONS = ("approve", "reject")


def _kpi_errors(field: str, values: dict, required: bool) -> list[str]:
    errors = []
    if required and not values:
        errors.append(f"'{field}' non può essere vuoto")
    for key, value in values.items():
        if key not in KPI_DEFS:
            errors.append(f"'{field}': indicatore sconosciuto '{key}'. Validi: {', '.join(KPI_DEFS)}")
        elif isinstance(value, bool) or not isinstance(value, NUMBER) or not math.isfinite(value):
            errors.append(f"'{field}.{key}' deve essere un numero")
    return errors


def check_schema(action: Action, ctx: GateContext) -> GateResult:
    spec = PARAMS.get(action.kind)
    if spec is None:
        return _block("schema", f"Strumento sconosciuto: {action.kind}")
    p, errors = action.params, []
    for name in p:
        if name not in spec:
            errors.append(f"Parametro non previsto: '{name}'")
    for name, (types, required) in spec.items():
        if name not in p:
            if required:
                errors.append(f"Parametro obbligatorio mancante: '{name}'")
            continue
        value = p[name]
        if isinstance(value, bool) or not isinstance(value, types):
            errors.append(f"'{name}' ha un tipo non valido")
        elif isinstance(value, str) and not value.strip():
            errors.append(f"'{name}' non può essere vuoto")
    if errors:
        return _block("schema", *errors)

    k = action.kind
    if k in ("approve_spend", "new_debt") and p["amount"] <= 0:
        errors.append("L'importo deve essere positivo")
    if k == "approve_spend":
        if p["category"] not in SPEND_CATEGORIES:
            errors.append(f"Categoria non valida: usare {' o '.join(SPEND_CATEGORIES)}")
        if not 1 <= p.get("useful_life_years", 10) <= 40:
            errors.append("La vita utile deve essere tra 1 e 40 anni")
    if k == "set_price":
        if p["customer"] not in ctx.customers:
            errors.append(f"Cliente sconosciuto: {p['customer']}. Validi: {', '.join(sorted(ctx.customers))}")
        if p["pct"] == 0 or abs(p["pct"]) > 0.5:
            errors.append("'pct' è una frazione diversa da zero tra -0.5 e 0.5 (es. 0.03 = +3%)")
    if k == "present_board_report":
        errors += _kpi_errors("kpis", p["kpis"], required=True)
    if k == "announce_to_staff":
        errors += _kpi_errors("numbers", p.get("numbers", {}), required=False)
    if k == "deliberate" and p["decision"] not in DECISIONS:
        errors.append("'decision' deve essere 'approve' o 'reject'")
    if k == "read_ledger" and "account" in p and p["account"] not in CHART:
        errors.append(f"Conto sconosciuto: {p['account']}. Validi: {', '.join(CHART)}")
    return _block("schema", *errors) if errors else _ok()


# ------------------------------------------------------------------ 2. role

def check_role(action: Action, ctx: GateContext) -> GateResult:
    role = ctx.rules["roles"].get(action.actor)
    if role is None:
        return _block("role", f"Ruolo sconosciuto: {action.actor}")
    if action.kind not in role["tools"]:
        return _block("role", f"{role['name']}: lo strumento '{action.kind}' non rientra nelle tue competenze")
    return _ok()


# ------------------------------------------------------------------ 3. delegation and thresholds

def is_related_party(name: str, rules: Mapping[str, Any]) -> bool:
    words = set(re.findall(r"\w+", name.lower()))
    rp = rules["related_parties"]
    return bool(words & set(rp["surnames"])) or any(normalise(x["name"]) == normalise(name) for x in rp["people"])


def check_delegation(action: Action, ctx: GateContext) -> GateResult:
    if action.actor != "uffici":
        return _ok()
    d, p, reasons = ctx.rules["delegation"], action.params, []
    if action.kind == "approve_spend":
        cp = normalise(p["counterparty"])
        total = ctx.spent_since_meeting.get(cp, 0.0) + p["amount"]
        if total >= d["spend_board_threshold"]:
            reasons.append(f"Spese verso '{p['counterparty']}' dall'ultimo CdA: {total:,.0f} EUR, "
                           f"oltre la delega di {d['spend_board_threshold']:,.0f} EUR")
        if is_related_party(p["counterparty"], ctx.rules):
            reasons.append(f"'{p['counterparty']}' è una parte correlata: serve sempre il CdA")
    elif action.kind == "set_price":
        cid = p["customer"]
        new = ctx.price_mult.get(cid, 1.0) * (1 + p["pct"])
        change = new / ctx.price_ref.get(cid, 1.0) - 1
        if abs(change) > d["price_change_max_pct"] + 1e-9:
            reasons.append(f"Variazione cumulata del prezzo di {cid} rispetto all'ultimo listino "
                           f"approvato: {change:+.1%}, oltre la delega di ±{d['price_change_max_pct']:.0%}")
    elif action.kind == "new_debt":
        reasons.append("Ogni nuovo debito richiede la delibera del CdA")
    return GateResult(NEEDS_APPROVAL, "delegation", tuple(reasons)) if reasons else _ok()


# ------------------------------------------------------------------ 4. procedure

def _period_error(p: Mapping[str, Any], week: int, first_allowed: int) -> str | None:
    if "week_from" not in p and "week_to" not in p:
        return None
    lo, hi = p.get("week_from", first_allowed), p.get("week_to", week)
    if not first_allowed <= lo <= hi <= week:
        return f"Periodo non valido: settimane {lo}-{hi}; sono chiuse le settimane {first_allowed}-{week}"
    return None


def check_procedure(action: Action, ctx: GateContext) -> GateResult:
    p, k = action.params, action.kind
    if k == "deliberate":
        if not ctx.meeting_open:
            return _block("procedure", "Il CdA può deliberare solo durante una riunione")
        if p["item_id"] not in ctx.open_items:
            return _block("procedure", f"Il punto {p['item_id']} non è all'ordine del giorno o è già deciso")
        if (action.actor, p["item_id"]) in ctx.conflicts:
            return _block("procedure", f"Hai dichiarato un conflitto di interessi sul punto {p['item_id']}: "
                                       "non puoi votare")
    if k == "declare_conflict" and "item_id" in p and p["item_id"] not in ctx.open_items:
        return _block("procedure", f"Il punto {p['item_id']} non è all'ordine del giorno")
    if k in ("present_board_report", "announce_to_staff", "get_kpis", "read_ledger"):
        error = _period_error(p, ctx.week, 0 if k == "read_ledger" else 1)
        if error:
            return _block("procedure", error)
    if k in ("set_price", "approve_spend", "new_debt"):
        for item_id, pending in ctx.open_items.items():
            if pending.kind == k and pending.params == p:
                return _block("procedure", f"Proposta identica già all'ordine del giorno (punto {item_id})")
    return _ok()


# ------------------------------------------------------------------ 5. law and CCNL

def check_law(action: Action, ctx: GateContext) -> GateResult:
    if action.kind in ("approve_spend", "new_debt"):
        text = normalise(" ".join(str(v) for v in action.params.values()))
        if any(word in text for word in ctx.rules["law"]["distribution_keywords"]):
            return _block("law", "Le distribuzioni ai soci sono di competenza dell'assemblea "
                                 "(art. 2433 c.c.): né gli Uffici né il CdA possono disporle")
    return _ok()


# ------------------------------------------------------------------ 6. resources

def check_resources(action: Action, ctx: GateContext) -> GateResult:
    if action.kind == "approve_spend" and action.params["amount"] > ctx.funds_available + 1e-6:
        return _block("resources", f"Risorse insufficienti: disponibili {ctx.funds_available:,.0f} EUR "
                                   f"tra cassa e fido, richiesti {action.params['amount']:,.0f} EUR")
    return _ok()


# ------------------------------------------------------------------ pipeline

PIPELINE: list[tuple[str, Callable[[Action, GateContext], GateResult]]] = [
    ("schema", check_schema),
    ("role", check_role),
    ("delegation", check_delegation),
    ("procedure", check_procedure),
    ("law", check_law),
    ("resources", check_resources),
]


def run(action: Action, ctx: GateContext, only: tuple[str, ...] | None = None) -> GateResult:
    """Run the pipeline (or only the named checks, e.g. law and resources again
    when the board approves an item)."""
    pending: list[GateResult] = []
    for name, check in PIPELINE:
        if only is not None and name not in only:
            continue
        result = check(action, ctx)
        if result.outcome == BLOCK:
            return result
        if result.outcome == NEEDS_APPROVAL:
            pending.append(result)
    if pending:
        return GateResult(NEEDS_APPROVAL, pending[0].check, tuple(r for x in pending for r in x.reasons))
    return _ok()
