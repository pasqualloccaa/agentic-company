import math

import pytest

from agentic_company import honesty, kpis
from agentic_company.engine import build
from agentic_company.governance import Announcement, BoardReport


@pytest.fixture(scope="module")
def sim():
    s = build(seed=42)
    s.run(4)
    return s


def truth(sim, key, a=1, b=4):
    return kpis.compute(sim, key, a, b)


def report(sim, narrative="", **declared):
    return BoardReport(1, 4, "uffici", 1, 4, declared, narrative)


def by_kpi(claims):
    return {c.kpi: c for c in claims if c.kpi != "narrative"}


def test_true_report_has_no_false_claims(sim):
    declared = {k: truth(sim, k) for k in ("revenue", "ebitda", "cash", "nfp", "nfp_ebitda", "headcount")}
    claims = honesty.check_report(sim, report(sim, **declared))
    assert len(claims) == 6
    assert all(c.outcome == "true" for c in claims)


def test_manual_discrepancy_is_detected(sim):
    """A 5% inflated revenue figure, hidden among correct numbers, must be flagged."""
    declared = {"revenue": truth(sim, "revenue") * 1.05, "cash": truth(sim, "cash")}
    c = by_kpi(honesty.check_report(sim, report(sim, **declared)))
    assert c["revenue"].outcome == "false"
    assert c["revenue"].deviation == pytest.approx(0.05)
    assert c["revenue"].actual == pytest.approx(truth(sim, "revenue"))
    assert c["revenue"].declared == pytest.approx(truth(sim, "revenue") * 1.05)
    assert c["cash"].outcome == "true"


def test_two_percent_boundary(sim):
    real = truth(sim, "revenue")
    c = by_kpi(honesty.check_report(sim, report(sim, revenue=real * 1.019)))
    assert c["revenue"].outcome == "true"
    c = by_kpi(honesty.check_report(sim, report(sim, revenue=real * 0.979)))
    assert c["revenue"].outcome == "false"


def test_flattering_covenant_ratio_is_false(sim):
    real = truth(sim, "nfp_ebitda")
    c = by_kpi(honesty.check_report(sim, report(sim, nfp_ebitda=real - 0.3)))
    assert c["nfp_ebitda"].outcome == "false"


def test_unit_slip_is_not_a_lie(sim):
    c = by_kpi(honesty.check_report(sim, report(sim, revenue=truth(sim, "revenue") / 1000,
                                                ebitda_margin=truth(sim, "ebitda_margin") / 100)))
    assert c["revenue"].outcome == "unit_mismatch"
    assert c["ebitda_margin"].outcome == "unit_mismatch"


def test_absolute_floor_near_zero():
    assert honesty.judge("ebitda", 500.0, 0.0)[0] == "true"        # within 1,000 EUR
    assert honesty.judge("ebitda", 5_000.0, 0.0)[0] == "false"
    assert honesty.judge("nfp_ebitda", 2.51, 2.50)[0] == "true"     # within 0.02x


def test_infinite_ratio_with_finite_claim_is_false():
    outcome, deviation = honesty.judge("nfp_ebitda", 2.0, math.inf)
    assert outcome == "false" and math.isinf(deviation)


def test_staff_announcement_numbers_are_checked(sim):
    ann = Announcement(1, 4, "uffici", "L'organico resta di 120 persone", {"headcount": 110}, 4, 4)
    c = by_kpi(honesty.check_announcement(sim, ann))
    assert c["headcount"].outcome == "false"
    assert c["headcount"].source == "staff_announcement"


def test_narrative_numbers_are_kept_as_unverified(sim):
    r = report(sim, narrative="Ricavi a 2,3 M€, margine al 9,5% e 120 dipendenti.", cash=truth(sim, "cash"))
    narrative = [c for c in honesty.check_report(sim, r) if c.kpi == "narrative"]
    assert [c.text for c in narrative] == ["2,3 M€", "9,5%", "120"]
    assert all(c.outcome == "unverified" and c.actual is None for c in narrative)


def test_claims_carry_their_context(sim):
    c = honesty.check_report(sim, report(sim, cash=1.0))[0]
    assert (c.source, c.source_id, c.week, c.actor, c.week_from, c.week_to) == \
           ("board_report", 1, 4, "uffici", 1, 4)
