import pytest

from agentic_company.ledger import Ledger, Line, UnbalancedEntryError


def test_balanced_entry_is_posted():
    L = Ledger()
    L.transfer(1, "Fattura", "receivables", "revenue", 1000)
    assert L.balance("receivables") == 1000
    assert L.balance("revenue") == 1000
    assert L.trial_balance() == 0


def test_unbalanced_entry_is_rejected():
    L = Ledger()
    with pytest.raises(UnbalancedEntryError):
        L.post(1, "Errata", [Line("cash", debit=100), Line("revenue", credit=90)])
    assert L.entries == []


def test_unknown_account_is_rejected():
    with pytest.raises(KeyError):
        Ledger().transfer(1, "x", "cash", "not_an_account", 10)


def test_negative_transfer_swaps_sides():
    L = Ledger()
    L.transfer(1, "Rettifica", "cash", "equity", -50)
    assert L.balance("cash") == -50
    assert L.balance("equity") == -50


def test_weekly_flows_only_count_that_week():
    L = Ledger()
    L.transfer(1, "a", "receivables", "revenue", 100)
    L.transfer(2, "b", "receivables", "revenue", 40)
    L.transfer(2, "c", "materials", "payables", 15)
    flows = L.flows(2)
    assert flows["revenue"] == 40
    assert flows["materials"] == 15
