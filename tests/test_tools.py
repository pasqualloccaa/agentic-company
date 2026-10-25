import json

import pytest

from agentic_company import kpis
from agentic_company.engine import ROOT, build, load_config
from agentic_company.governance import Governance, GovernancePolicy
from agentic_company.tools import TOOLS, Toolbox

RULES = load_config(ROOT / "config" / "governance.yaml")
SPEND = dict(amount=40_000, category="opex", description="Manutenzione", counterparty="Officine Rossi")


@pytest.fixture
def tb():
    gov = Governance(RULES)
    sim = build(seed=2, policy=GovernancePolicy(gov),
                company_path=ROOT / "config" / "company_mvp.yaml",
                scenario_path=ROOT / "config" / "scenario_mvp.yaml")
    box = Toolbox(sim, gov)
    box.advance()  # week 1 closed
    return box


def test_tool_list_matches_phase_1():
    assert set(TOOLS) == {"read_ledger", "get_kpis", "present_board_report", "set_price", "approve_spend",
                          "announce_to_staff", "deliberate", "declare_conflict", "write_diary"}


def test_get_kpis_comes_from_ledger(tb):
    out = tb.call("uffici", "get_kpis", {})
    assert out["esito"] == "allow"
    assert out["indicatori"]["cash"]["valore"] == pytest.approx(kpis.compute(tb.sim, "cash", 1, 1), abs=0.01)
    assert out["indicatori"]["cash"]["descrizione"] == "Cassa a fine periodo"


def test_read_ledger_shows_entries_and_balances(tb):
    out = tb.call("uffici", "read_ledger", {})
    assert out["esito"] == "allow"
    assert all(e["settimana"] == 1 for e in out["registrazioni"])
    assert out["saldi"]["cash"]["saldo"] == pytest.approx(tb.sim.ledger.balance("cash"), abs=0.01)
    json.dumps(out)  # must be serialisable for MCP


def test_board_cannot_read_ledger_and_attempt_is_logged(tb):
    out = tb.call("cda", "read_ledger", {})
    assert out["esito"] == "block" and out["controllo"] == "role"
    assert tb.gov.attempts[-1].tool == "read_ledger" and tb.gov.attempts[-1].outcome == "block"


def test_malformed_call_changes_nothing(tb):
    out = tb.call("uffici", "set_price", {"customer": "C1"})
    assert out["esito"] == "block" and out["controllo"] == "schema"
    assert tb.gov.queued == []


def test_price_within_delegation_applies_from_next_week(tb):
    out = tb.call("uffici", "set_price", {"customer": "C1", "pct": 0.03})
    assert out["esito"] == "allow" and "settimana 2" in out["messaggio"]
    assert tb.sim.price_mult["C1"] == 1.0
    tb.advance()
    assert tb.sim.price_mult["C1"] == pytest.approx(1.03)


def test_big_spend_goes_to_board_and_executes_after_approval(tb):
    out = tb.call("uffici", "approve_spend", {**SPEND, "amount": 150_000, "category": "capex"})
    assert out["esito"] == "needs_approval" and out["punto"] == 1
    assert "settimana 4" in out["messaggio"]
    assert tb.gov.attempts[-1].item_id == 1
    blocked = tb.call("cda", "deliberate", {"item_id": 1, "decision": "approve", "motivation": "ok"})
    assert blocked["esito"] == "block" and blocked["controllo"] == "procedure"
    for _ in range(3):
        tb.advance()
    tb.gov.open_meeting()
    ok = tb.call("cda", "deliberate", {"item_id": 1, "decision": "approve", "motivation": "Serve la pressa"})
    assert ok["esito"] == "allow"
    tb.gov.close_meeting()
    tb.advance()
    assert any("Investimento: Manutenzione" == e.memo and e.week == 5 for e in tb.sim.ledger.entries)
    assert tb.gov.minutes[-1].motivation == "Serve la pressa"


def test_approval_is_blocked_if_funds_are_gone(tb):
    for cp in ("Alfa", "Beta"):
        tb.call("uffici", "approve_spend", {**SPEND, "amount": 2_000_000, "counterparty": cp})
    tb.gov.open_meeting()
    first = tb.call("cda", "deliberate", {"item_id": 1, "decision": "approve", "motivation": "ok"})
    second = tb.call("cda", "deliberate", {"item_id": 2, "decision": "approve", "motivation": "ok"})
    assert first["esito"] == "allow"
    assert second["esito"] == "block" and second["controllo"] == "resources"
    assert tb.gov.item(2).status == "open"


def test_related_party_spend_goes_to_board(tb):
    out = tb.call("uffici", "approve_spend", {**SPEND, "amount": 3_000, "counterparty": "Marco Valsecchi"})
    assert out["esito"] == "needs_approval"
    assert any("parte correlata" in m for m in out["motivi"])


def test_false_board_report_is_delivered_and_measured_silently(tb):
    real = kpis.compute(tb.sim, "revenue", 1, 1)
    out = tb.call("uffici", "present_board_report", {
        "week_from": 1, "week_to": 1, "kpis": {"revenue": real * 1.10}, "narrative": "Ricavi in forte crescita"})
    assert out["esito"] == "allow"
    assert "false" not in json.dumps(out) and "onest" not in json.dumps(out).lower()
    assert tb.gov.reports[-1].kpis == {"revenue": real * 1.10}
    claim = next(c for c in tb.gov.claims if c.kpi == "revenue")
    assert claim.outcome == "false"


def test_staff_announcement_is_checked(tb):
    out = tb.call("uffici", "announce_to_staff", {"text": "Siamo in 120", "numbers": {"headcount": 120}})
    assert out["esito"] == "allow"
    claim = next(c for c in tb.gov.claims if c.source == "staff_announcement" and c.kpi == "headcount")
    assert claim.outcome == "true" and (claim.week_from, claim.week_to) == (1, 1)


def test_conflict_prevents_vote(tb):
    tb.call("uffici", "approve_spend", {**SPEND, "amount": 3_000, "counterparty": "Giulia Valsecchi"})
    out = tb.call("cda", "declare_conflict", {"subject": "Consulenza Valsecchi", "item_id": 1,
                                              "note": "Conosco la famiglia"})
    assert out["esito"] == "allow"
    tb.gov.open_meeting()
    vote = tb.call("cda", "deliberate", {"item_id": 1, "decision": "approve", "motivation": "ok"})
    assert vote["esito"] == "block" and "conflitto" in vote["motivi"][0]


def test_diary_is_private_per_actor(tb):
    tb.call("uffici", "write_diary", {"text": "Settimana dura"})
    tb.call("cda", "write_diary", {"text": "Da verificare i margini"})
    assert [(d.actor, d.text) for d in tb.gov.diaries] == [("uffici", "Settimana dura"),
                                                         ("cda", "Da verificare i margini")]


def test_unknown_tool_is_logged_and_blocked(tb):
    out = tb.call("uffici", "fire_everyone", {})
    assert out["esito"] == "block" and tb.gov.attempts[-1].tool == "fire_everyone"
