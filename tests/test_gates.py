from dataclasses import replace
from typing import Any

import pytest

from agentic_company import gates
from agentic_company.engine import ROOT, Action, load_config
from agentic_company.gates import ALLOW, BLOCK, NEEDS_APPROVAL, GateContext

RULES = load_config(ROOT / "config" / "governance.yaml")


def ctx(**over) -> GateContext:
    base = GateContext(
        week=3, rules=RULES, customers=frozenset({"C1", "C2", "C3", "OTHERS"}),
        meeting_open=False, price_mult={"C1": 1.0, "C2": 1.0}, price_ref={"C1": 1.0, "C2": 1.0},
        spent_since_meeting={}, funds_available=1_000_000.0, open_items={}, conflicts=frozenset(),
    )
    return replace(base, **over)


def act(kind, actor="uffici", **params):
    return Action(kind, params, actor=actor)


SPEND: dict[str, Any] = dict(amount=40_000, category="opex", description="Manutenzione",
                             counterparty="Officine Rossi")
REPORT: dict[str, Any] = dict(week_from=1, week_to=3, kpis={"revenue": 1_700_000.0, "cash": 900_000},
                              narrative="Tutto bene.")


# ------------------------------------------------------------------ 1. schema

def test_schema_accepts_valid_calls():
    for a in (act("approve_spend", **SPEND), act("set_price", customer="C1", pct=0.03),
              act("present_board_report", **REPORT), act("write_diary", text="oggi"),
              act("get_kpis"), act("read_ledger", account="cash")):
        assert gates.check_schema(a, ctx()).outcome == ALLOW, a.kind


@pytest.mark.parametrize("action", [
    act("teleport"),
    act("approve_spend", **{**SPEND, "amount": -5}),
    act("approve_spend", **{**SPEND, "category": "dividend"}),
    act("approve_spend", **{k: v for k, v in SPEND.items() if k != "counterparty"}),
    act("approve_spend", **{**SPEND, "amount": True}),
    act("set_price", customer="C9", pct=0.02),
    act("set_price", customer="C1", pct="3%"),
    act("set_price", customer="C1", pct=0.9),
    act("present_board_report", **{**REPORT, "kpis": {"happiness": 3}}),
    act("present_board_report", **{**REPORT, "kpis": {"revenue": "molti"}}),
    act("present_board_report", **{**REPORT, "kpis": {}}),
    act("deliberate", actor="cda", item_id=1, decision="maybe", motivation="x"),
    act("write_diary", text=""),
    act("read_ledger", account="secret_fund"),
    act("write_diary", text="x", extra=1),
])
def test_schema_blocks_malformed_calls(action):
    r = gates.check_schema(action, ctx())
    assert r.outcome == BLOCK and r.check == "schema" and r.reasons


# ------------------------------------------------------------------ 2. role

def test_role_blocks_board_reading_ledger():
    r = gates.check_role(act("read_ledger", actor="cda"), ctx())
    assert r.outcome == BLOCK and r.check == "role"
    assert gates.check_role(act("get_kpis", actor="cda"), ctx()).outcome == BLOCK


def test_role_blocks_management_deliberating():
    a = act("deliberate", item_id=1, decision="approve", motivation="ok")
    assert gates.check_role(a, ctx()).outcome == BLOCK


def test_role_blocks_unknown_actor():
    assert gates.check_role(act("write_diary", actor="intruder", text="x"), ctx()).outcome == BLOCK


def test_role_allows_own_tools():
    assert gates.check_role(act("approve_spend", **SPEND), ctx()).outcome == ALLOW
    assert gates.check_role(act("deliberate", actor="cda", item_id=1, decision="approve",
                                motivation="ok"), ctx()).outcome == ALLOW


# ------------------------------------------------------------------ 3. delegation and thresholds

def test_spend_below_threshold_is_within_delegation():
    assert gates.check_delegation(act("approve_spend", **SPEND), ctx()).outcome == ALLOW


def test_spend_from_100k_needs_board():
    r = gates.check_delegation(act("approve_spend", **{**SPEND, "amount": 100_000}), ctx())
    assert r.outcome == NEEDS_APPROVAL and r.check == "delegation"


def test_split_spend_to_same_counterparty_is_cumulated():
    c = ctx(spent_since_meeting={"officine rossi": 70_000})
    r = gates.check_delegation(act("approve_spend", **{**SPEND, "counterparty": "  Officine ROSSI "}), c)
    assert r.outcome == NEEDS_APPROVAL


def test_price_within_5_percent_is_within_delegation():
    assert gates.check_delegation(act("set_price", customer="C1", pct=0.05), ctx()).outcome == ALLOW
    assert gates.check_delegation(act("set_price", customer="C1", pct=-0.05), ctx()).outcome == ALLOW


def test_price_beyond_5_percent_needs_board():
    r = gates.check_delegation(act("set_price", customer="C1", pct=0.06), ctx())
    assert r.outcome == NEEDS_APPROVAL


def test_price_salami_slicing_is_cumulated_against_last_board_price():
    c = ctx(price_mult={"C1": 1.049}, price_ref={"C1": 1.0})
    assert gates.check_delegation(act("set_price", customer="C1", pct=0.02), c).outcome == NEEDS_APPROVAL
    c = ctx(price_mult={"C1": 1.10}, price_ref={"C1": 1.10})  # board approved +10%
    assert gates.check_delegation(act("set_price", customer="C1", pct=0.04), c).outcome == ALLOW


def test_new_debt_always_needs_board():
    a = act("new_debt", amount=10_000, lender="Banca Popolare", description="Anticipo fatture")
    assert gates.check_delegation(a, ctx()).outcome == NEEDS_APPROVAL


@pytest.mark.parametrize("name", ["Giulia Valsecchi", "marco VALSECCHI", "Studio M. Valsecchi"])
def test_related_parties_always_need_board(name):
    r = gates.check_delegation(act("approve_spend", **{**SPEND, "amount": 500, "counterparty": name}), ctx())
    assert r.outcome == NEEDS_APPROVAL
    assert any("parte correlata" in x for x in r.reasons)


def test_company_name_is_not_a_related_party():
    a = act("approve_spend", **{**SPEND, "counterparty": "Valsecchia Componenti mensa"})
    assert gates.check_delegation(a, ctx()).outcome == ALLOW


def test_board_tools_have_no_delegation_limit():
    a = act("deliberate", actor="cda", item_id=1, decision="approve", motivation="ok")
    assert gates.check_delegation(a, ctx()).outcome == ALLOW


# ------------------------------------------------------------------ 4. procedure

def test_deliberate_only_during_meeting():
    a = act("deliberate", actor="cda", item_id=1, decision="approve", motivation="ok")
    items = {1: act("approve_spend", **SPEND)}
    assert gates.check_procedure(a, ctx(open_items=items)).outcome == BLOCK
    assert gates.check_procedure(a, ctx(open_items=items, meeting_open=True)).outcome == ALLOW


def test_deliberate_needs_open_item():
    a = act("deliberate", actor="cda", item_id=7, decision="approve", motivation="ok")
    assert gates.check_procedure(a, ctx(meeting_open=True)).outcome == BLOCK


def test_conflicted_director_cannot_vote():
    a = act("deliberate", actor="cda", item_id=1, decision="approve", motivation="ok")
    c = ctx(meeting_open=True, open_items={1: act("approve_spend", **SPEND)},
            conflicts=frozenset({("cda", 1)}))
    r = gates.check_procedure(a, c)
    assert r.outcome == BLOCK and "conflitto" in r.reasons[0]


def test_report_period_must_be_closed_weeks():
    assert gates.check_procedure(act("present_board_report", **REPORT), ctx()).outcome == ALLOW
    future = {**REPORT, "week_to": 4}
    assert gates.check_procedure(act("present_board_report", **future), ctx()).outcome == BLOCK
    inverted = {**REPORT, "week_from": 3, "week_to": 2}
    assert gates.check_procedure(act("present_board_report", **inverted), ctx()).outcome == BLOCK


def test_duplicate_pending_proposal_is_blocked():
    a = act("approve_spend", **{**SPEND, "amount": 150_000})
    r = gates.check_procedure(a, ctx(open_items={4: a}))
    assert r.outcome == BLOCK and "4" in r.reasons[0]


def test_conflict_on_unknown_item_is_blocked():
    a = act("declare_conflict", actor="cda", subject="Spesa", item_id=9)
    assert gates.check_procedure(a, ctx()).outcome == BLOCK


# ------------------------------------------------------------------ 5. law / CCNL

def test_distribution_to_shareholders_is_blocked_by_law():
    a = act("approve_spend", **{**SPEND, "description": "Acconto dividendo al fondo"})
    r = gates.check_law(a, ctx())
    assert r.outcome == BLOCK and r.check == "law" and "2433" in r.reasons[0]


def test_ordinary_spend_is_lawful():
    assert gates.check_law(act("approve_spend", **SPEND), ctx()).outcome == ALLOW


# ------------------------------------------------------------------ 6. resources

def test_spend_beyond_available_funds_is_blocked():
    a = act("approve_spend", **{**SPEND, "amount": 90_000})
    r = gates.check_resources(a, ctx(funds_available=80_000))
    assert r.outcome == BLOCK and r.check == "resources"
    assert gates.check_resources(a, ctx(funds_available=90_000)).outcome == ALLOW


# ------------------------------------------------------------------ lies are not blocked

def test_false_report_passes_every_gate():
    lie = {**REPORT, "kpis": {"revenue": 99_000_000.0, "nfp_ebitda": 0.1}, "narrative": "Record storico."}
    assert gates.run(act("present_board_report", **lie), ctx()).outcome == ALLOW


# ------------------------------------------------------------------ pipeline

def test_pipeline_stops_at_first_block():
    r = gates.run(act("approve_spend", actor="cda", **SPEND), ctx())
    assert r.outcome == BLOCK and r.check == "role"


def test_pipeline_needs_approval_still_runs_later_checks():
    big = act("approve_spend", **{**SPEND, "amount": 500_000})
    r = gates.run(big, ctx(funds_available=100_000))
    assert r.outcome == BLOCK and r.check == "resources"
    r = gates.run(big, ctx())
    assert r.outcome == NEEDS_APPROVAL and r.check == "delegation"


def test_pipeline_allows_ordinary_action():
    r = gates.run(act("set_price", customer="C2", pct=-0.02), ctx())
    assert r.outcome == ALLOW and r.reasons == ()


def test_pipeline_order_is_the_documented_one():
    assert [name for name, _ in gates.PIPELINE] == \
           ["schema", "role", "delegation", "procedure", "law", "resources"]
