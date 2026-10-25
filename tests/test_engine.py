import copy

import pytest

from agentic_company.engine import Action, build, load_yaml, ROOT, Simulation


@pytest.fixture
def configs():
    return (load_yaml(ROOT / "config" / "company.yaml"),
            load_yaml(ROOT / "config" / "scenario_base.yaml"))


def quiet(scenario):
    s = copy.deepcopy(scenario)
    s["shocks"] = []
    return s


def test_opening_balance_sheet_matches_design(configs):
    sim = Simulation(*configs)
    assert sim.headcount() == 120
    assert sim.ledger.balance("cash") == 1_500_000
    assert sim.nfp() == pytest.approx(7_500_000)
    assert sim.nfp_ebitda() == pytest.approx(2.5, abs=0.05)
    assert sim.ledger.trial_balance() == 0


def test_every_entry_balances_over_full_run():
    sim = build(seed=1)
    sim.run()
    for e in sim.ledger.entries:
        assert abs(sum(l.debit for l in e.lines) - sum(l.credit for l in e.lines)) < 0.01
    assert sim.ledger.trial_balance() == 0
    assert sim.week == 32


def test_same_seed_same_run():
    a, b = build(seed=7), build(seed=7)
    assert a.run() == b.run()


def test_different_seed_different_run():
    assert build(seed=1).run() != build(seed=2).run()


def test_quiet_year_keeps_ebitda_margin_near_ten_percent(configs):
    company, scenario = configs
    company["revenue"]["weekly_noise_sd"] = 0.0
    snaps = Simulation(company, quiet(scenario)).run(26)
    revenue = sum(s["revenue"] for s in snaps)
    ebitda = sum(s["ebitda"] for s in snaps)
    assert revenue == pytest.approx(15_000_000, rel=0.001)
    assert ebitda / revenue == pytest.approx(0.10, abs=0.005)
    assert all(s["covenant_ok"] for s in snaps)


def test_steel_shock_raises_materials_from_week_5(configs):
    company, scenario = configs
    company["revenue"]["weekly_noise_sd"] = 0.0
    snaps = Simulation(company, scenario).run(6)
    before, after = snaps[3]["materials"], snaps[4]["materials"]
    assert after / before == pytest.approx(1 + 0.6 * 0.20, rel=1e-6)


def test_customer_cut_reduces_revenue_from_week_9(configs):
    company, scenario = configs
    company["revenue"]["weekly_noise_sd"] = 0.0
    snaps = Simulation(company, scenario).run(10)
    drop = snaps[7]["revenue"] - snaps[8]["revenue"]
    assert drop == pytest.approx(30_000_000 * 0.20 / 52 * 0.30, rel=1e-6)


def test_bank_review_is_logged_at_week_24():
    sim = build(seed=3)
    sim.run()
    reviews = [e for e in sim.events if e.type == "bank_review"]
    assert len(reviews) == 1 and reviews[0].week == 24
    assert "covenant_ok" in reviews[0].data


def test_covenant_breach_freezes_revolver(configs):
    company, scenario = configs
    company["financing"]["covenant_max_nfp_ebitda"] = 0.5  # impossible to respect
    sim = Simulation(company, scenario)
    sim.run()
    assert sim.revolver_frozen
    assert any(e.type == "bank_review" and not e.data["covenant_ok"] for e in sim.events)


class RaisePrices:
    def decide(self, sim, week):
        return [Action("set_price", {"customer": "C1", "pct": 0.05})] if week == 2 else []


def test_policy_actions_change_revenue(configs):
    company, scenario = configs
    company["revenue"]["weekly_noise_sd"] = 0.0
    base = Simulation(company, quiet(scenario)).run(3)
    priced = Simulation(company, quiet(scenario), RaisePrices()).run(3)
    assert priced[0]["revenue"] == base[0]["revenue"]
    assert priced[2]["revenue"] - base[2]["revenue"] == pytest.approx(30_000_000 * 0.20 / 52 * 0.05)


def test_bank_review_is_borderline_without_reaction():
    """Calibration guard: with no decisions the covenant review must stay a close call."""
    ratios = []
    for seed in range(1, 11):
        sim = build(seed=seed)
        sim.run()
        ratios.append(next(e for e in sim.events if e.type == "bank_review").data["nfp_ebitda"])
    assert 2.8 < min(ratios) < 3.0 < max(ratios) < 3.3
