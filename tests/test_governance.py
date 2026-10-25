import pytest

from agentic_company.engine import ROOT, Action, build, load_config
from agentic_company.gates import ALLOW, BLOCK, GateResult
from agentic_company.governance import Governance, GovernancePolicy, to_engine

RULES = load_config(ROOT / "config" / "governance.yaml")
SPEND = dict(amount=40_000, category="opex", description="Manutenzione", counterparty="Officine Rossi")


@pytest.fixture
def world():
    gov = Governance(RULES)
    sim = build(seed=1, policy=GovernancePolicy(gov))
    sim.step()
    gov.week = sim.week
    return sim, gov


def test_every_attempt_is_recorded_even_if_blocked(world):
    _, gov = world
    gov.record(Action("read_ledger", {}, "cda"), GateResult(BLOCK, "role", ("no",)))
    gov.record(Action("write_diary", {"text": "x"}, "uffici"), GateResult(ALLOW))
    assert [(a.seq, a.tool, a.outcome, a.check) for a in gov.attempts] == \
           [(1, "read_ledger", "block", "role"), (2, "write_diary", "allow", "")]
    assert gov.attempts[0].week == 1


def test_context_reflects_simulation(world):
    sim, gov = world
    ctx = gov.context(sim)
    assert ctx.week == 1
    assert ctx.customers == frozenset(sim.customers)
    assert ctx.funds_available == pytest.approx(
        sim.ledger.balance("cash") + sim.revolver_limit - sim.ledger.balance("revolver"))
    assert ctx.price_ref["C1"] == 1.0 and not ctx.meeting_open


def test_queued_actions_count_in_context_and_reach_the_engine(world):
    sim, gov = world
    gov.queue(Action("set_price", {"customer": "C1", "pct": 0.04}, "uffici"))
    gov.queue(Action("approve_spend", dict(SPEND), "uffici"))
    gov.record(Action("approve_spend", dict(SPEND), "uffici"), GateResult(ALLOW))
    ctx = gov.context(sim)
    assert ctx.price_mult["C1"] == pytest.approx(1.04)
    assert ctx.spent_since_meeting == {"officine rossi": 40_000}
    liquidity = sim.ledger.balance("cash") + sim.revolver_limit - sim.ledger.balance("revolver")
    assert ctx.funds_available == pytest.approx(liquidity - 40_000)
    sim.step()
    gov.week = sim.week
    assert sim.price_mult["C1"] == pytest.approx(1.04)
    assert any(e.origin == "uffici" and "Manutenzione" in e.memo for e in sim.ledger.entries)
    assert gov.queued == []


def test_spend_counter_resets_after_board_meeting(world):
    sim, gov = world
    gov.record(Action("approve_spend", dict(SPEND), "uffici"), GateResult(ALLOW))
    gov.open_meeting()
    gov.close_meeting()
    gov.week += 1
    assert gov.context(sim).spent_since_meeting == {}


def test_agenda_item_lifecycle_writes_minutes(world):
    sim, gov = world
    big = Action("approve_spend", {**SPEND, "amount": 150_000}, "uffici")
    item = gov.open_item(big, ("oltre delega",))
    assert item.item_id == 1 and item.status == "open"
    assert gov.context(sim).open_items == {1: big}
    gov.open_meeting()
    assert gov.context(sim).meeting_open
    gov.decide(1, "approve", "Investimento necessario", "cda", sim)
    assert item.status == "approved"
    assert gov.minutes[-1].item_id == 1 and gov.minutes[-1].decision == "approve"
    assert gov.context(sim).open_items == {}
    assert gov.queued == [big]


def test_rejected_item_is_not_executed(world):
    sim, gov = world
    gov.open_item(Action("set_price", {"customer": "C1", "pct": 0.10}, "uffici"), ("oltre delega",))
    gov.open_meeting()
    gov.decide(1, "reject", "Troppo aggressivo", "cda", sim)
    assert gov.queued == [] and gov.agenda[0].status == "rejected"


def test_board_approved_price_becomes_new_reference(world):
    sim, gov = world
    gov.open_item(Action("set_price", {"customer": "C1", "pct": 0.10}, "uffici"), ("oltre delega",))
    gov.open_meeting()
    gov.decide(1, "approve", "Rincaro acciaio", "cda", sim)
    ctx = gov.context(sim)
    assert ctx.price_ref["C1"] == pytest.approx(1.10)
    assert ctx.price_mult["C1"] == pytest.approx(1.10)


def test_conflicts_are_visible_to_gates(world):
    sim, gov = world
    gov.open_item(Action("approve_spend", {**SPEND, "counterparty": "Giulia Valsecchi"}, "uffici"), ("pc",))
    gov.declare_conflict("cda", "Consulenza Valsecchi", 1, "Amico di famiglia")
    assert ("cda", 1) in gov.context(sim).conflicts


def test_to_engine_translates_tools():
    assert to_engine(Action("approve_spend", dict(SPEND), "uffici")) == Action("spend", dict(SPEND), "uffici")
    p = {"customer": "C2", "pct": -0.02}
    assert to_engine(Action("set_price", p, "uffici")) == Action("set_price", p, "uffici")
    with pytest.raises(ValueError):
        to_engine(Action("write_diary", {"text": "x"}, "uffici"))
