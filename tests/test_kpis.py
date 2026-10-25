import copy

import pytest

from agentic_company import kpis
from agentic_company.engine import ROOT, Action, Simulation, build, load_config


@pytest.fixture
def configs():
    return (load_config(ROOT / "config" / "company.yaml"),
            load_config(ROOT / "config" / "scenario_base.yaml"))


def test_mvp_config_aggregates_departments_without_changing_costs():
    base = load_config(ROOT / "config" / "company.yaml")
    mvp = load_config(ROOT / "config" / "company_mvp.yaml")
    assert len(mvp["personnel"]["departments"]) == 3
    assert "base" not in mvp
    hc = lambda c: sum(d["headcount"] for d in c["personnel"]["departments"])
    cost = lambda c: sum(d["headcount"] * d["cost_per_fte"] for d in c["personnel"]["departments"])
    assert hc(mvp) == hc(base) == 120
    assert cost(mvp) == pytest.approx(cost(base), abs=0.01)
    assert {k: v for k, v in mvp.items() if k != "personnel"} == \
           {k: v for k, v in base.items() if k != "personnel"}


def test_mvp_company_runs_like_base_company():
    """Aggregating departments must not change the phase 0 calibration."""
    base = build(seed=42).run()
    mvp = build(seed=42, company_path=ROOT / "config" / "company_mvp.yaml").run()
    for a, b in zip(base, mvp):
        for key, value in a.items():
            assert b[key] == pytest.approx(value, abs=0.05), (a["week"], key)


def test_mvp_scenario_has_eight_weeks_and_announces_week_9_cut():
    sc = load_config(ROOT / "config" / "scenario_mvp.yaml")
    base = load_config(ROOT / "config" / "scenario_base.yaml")
    assert sc["weeks"] == 8
    weeks = {s["week"]: s for s in sc["shocks"]}
    assert weeks[5] == next(s for s in base["shocks"] if s["week"] == 5)
    assert weeks[9] == next(s for s in base["shocks"] if s["week"] == 9)
    assert weeks[6]["type"] == "event"


@pytest.mark.parametrize("seed", [1, 42])
def test_kpis_match_engine_snapshots(seed):
    sim = build(seed=seed)
    sim.run()
    for snap in sim.snapshots:
        w = snap["week"]
        got = kpis.compute_all(sim, w, w)
        for key in ("revenue", "materials", "personnel", "other_opex", "ebitda", "net_result",
                    "cash", "receivables", "payables", "term_loan", "revolver", "nfp",
                    "ebitda_ltm", "headcount"):
            assert got[key] == pytest.approx(snap[key], abs=0.02), (w, key)
        assert got["nfp_ebitda"] == pytest.approx(snap["nfp_ebitda"], abs=0.001)


def test_period_flows_sum_weeks():
    sim = build(seed=5)
    sim.run(8)
    got = kpis.compute_all(sim, 5, 8)
    assert got["revenue"] == pytest.approx(sum(s["revenue"] for s in sim.snapshots[4:8]), abs=0.05)
    assert got["ebitda_margin"] == pytest.approx(100 * got["ebitda"] / got["revenue"])


def test_stock_kpis_are_read_at_end_of_period():
    sim = build(seed=5)
    sim.run(8)
    assert kpis.compute_all(sim, 1, 4)["cash"] == pytest.approx(sim.snapshots[3]["cash"], abs=0.01)


def test_every_kpi_has_italian_label():
    for key, d in kpis.KPI_DEFS.items():
        assert d.label and d.unit in ("EUR", "%", "x", "FTE"), key


def test_unknown_kpi_is_rejected():
    sim = build(seed=1)
    sim.run(2)
    with pytest.raises(KeyError):
        kpis.compute(sim, "profit_magic", 1, 2)


# ------------------------------------------------------------------ spend action

def _quiet(company, scenario):
    company = copy.deepcopy(company)
    company["revenue"]["weekly_noise_sd"] = 0.0
    scenario = copy.deepcopy(scenario)
    scenario["shocks"] = []
    return company, scenario


class SpendOnce:
    def __init__(self, **params):
        self.params = params

    def decide(self, sim, week):
        return [Action("spend", self.params, actor="uffici")] if week == 2 else []


def test_opex_spend_hits_ebitda_and_cash_after_payable_period(configs):
    company, scenario = _quiet(*configs)
    base = Simulation(company, scenario)
    base.run(10)
    spent = Simulation(company, scenario, SpendOnce(amount=50_000, category="opex",
                                                    description="Consulenza"))
    spent.run(10)
    assert spent.snapshots[1]["ebitda"] == pytest.approx(base.snapshots[1]["ebitda"] - 50_000)
    assert spent.snapshots[0]["ebitda"] == base.snapshots[0]["ebitda"]
    # paid after 8 weeks: net debt identical until week 9, higher by the amount from week 10
    nfp = lambda sim, w: kpis.compute(sim, "nfp", w, w)
    assert nfp(spent, 9) == pytest.approx(nfp(base, 9), abs=0.01)
    assert nfp(spent, 10) - nfp(base, 10) == pytest.approx(50_000, abs=0.01)
    assert any(e.origin == "uffici" for e in spent.ledger.entries)
    assert spent.ledger.trial_balance() == 0


def test_capex_spend_is_capitalised_and_depreciated(configs):
    company, scenario = _quiet(*configs)
    base = Simulation(company, scenario)
    base.run(4)
    spent = Simulation(company, scenario, SpendOnce(amount=520_000, category="capex",
                                                    description="Nuova pressa", useful_life_years=10))
    spent.run(4)
    assert spent.snapshots[1]["ebitda"] == pytest.approx(base.snapshots[1]["ebitda"])
    extra_depr = 520_000 / (10 * 52)
    assert spent.ledger.flows(3)["depreciation"] == pytest.approx(base.ledger.flows(3)["depreciation"] + extra_depr)
    assert spent.ledger.balance("fixed_assets") == pytest.approx(
        base.ledger.balance("fixed_assets") + 520_000 - 3 * extra_depr, abs=0.05)


def test_spend_rejects_unknown_category(configs):
    company, scenario = _quiet(*configs)
    sim = Simulation(company, scenario, SpendOnce(amount=1, category="dividend", description="x"))
    with pytest.raises(ValueError):
        sim.run(2)


def test_balance_at_matches_history():
    sim = build(seed=3)
    sim.run(6)
    for s in sim.snapshots:
        assert sim.ledger.balance_at("cash", s["week"]) == pytest.approx(s["cash"], abs=0.01)
