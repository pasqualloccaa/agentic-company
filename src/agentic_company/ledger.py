"""Double-entry ledger: the single source of truth of the simulation.

Every economic fact is a balanced journal entry. Reports, KPIs and (later) the
honesty checks are all computed from here, never stored separately.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AccountType(str, Enum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"


# Chart of accounts: code -> (type, description)
CHART: dict[str, tuple[AccountType, str]] = {
    "cash": (AccountType.ASSET, "Cassa e banche"),
    "receivables": (AccountType.ASSET, "Crediti verso clienti"),
    "fixed_assets": (AccountType.ASSET, "Immobilizzazioni nette"),
    "payables": (AccountType.LIABILITY, "Debiti verso fornitori"),
    "payroll_payable": (AccountType.LIABILITY, "Debiti verso dipendenti"),
    "term_loan": (AccountType.LIABILITY, "Finanziamento a medio termine"),
    "revolver": (AccountType.LIABILITY, "Fido bancario utilizzato"),
    "equity": (AccountType.EQUITY, "Patrimonio netto"),
    "revenue": (AccountType.REVENUE, "Ricavi di vendita"),
    "materials": (AccountType.EXPENSE, "Materie prime"),
    "personnel": (AccountType.EXPENSE, "Costo del personale"),
    "other_opex": (AccountType.EXPENSE, "Altri costi operativi"),
    "depreciation": (AccountType.EXPENSE, "Ammortamenti"),
    "interest": (AccountType.EXPENSE, "Oneri finanziari"),
}

EBITDA_EXPENSES = ("materials", "personnel", "other_opex")


class UnbalancedEntryError(ValueError):
    pass


@dataclass(frozen=True)
class Line:
    account: str
    debit: float = 0.0
    credit: float = 0.0


@dataclass(frozen=True)
class Entry:
    entry_id: int
    week: int
    memo: str
    origin: str  # who caused it: "engine", "opening", later an agent id
    lines: tuple[Line, ...]


@dataclass
class Ledger:
    entries: list[Entry] = field(default_factory=list)
    _balances: dict[str, float] = field(default_factory=lambda: {a: 0.0 for a in CHART})

    def post(self, week: int, memo: str, lines: list[Line], origin: str = "engine") -> Entry:
        for line in lines:
            if line.account not in CHART:
                raise KeyError(f"Unknown account: {line.account}")
            if line.debit < 0 or line.credit < 0:
                raise ValueError("Debit and credit amounts must be non-negative")
        debits = round(sum(l.debit for l in lines), 2)
        credits = round(sum(l.credit for l in lines), 2)
        if abs(debits - credits) > 0.01:
            raise UnbalancedEntryError(f"{memo}: debits {debits} != credits {credits}")
        entry = Entry(len(self.entries) + 1, week, memo, origin, tuple(lines))
        self.entries.append(entry)
        for line in lines:
            self._balances[line.account] += line.debit - line.credit
        return entry

    def transfer(self, week: int, memo: str, debit: str, credit: str, amount: float,
                 origin: str = "engine") -> Entry | None:
        """Shortcut for a two-line entry. Zero amounts are skipped."""
        amount = round(amount, 2)
        if amount == 0:
            return None
        if amount < 0:
            debit, credit, amount = credit, debit, -amount
        return self.post(week, memo, [Line(debit, debit=amount), Line(credit, credit=amount)], origin)

    def balance(self, account: str) -> float:
        """Natural-sign balance: positive for assets/expenses (debit) and for
        liabilities/equity/revenue (credit)."""
        raw = self._balances[account]
        kind = CHART[account][0]
        return raw if kind in (AccountType.ASSET, AccountType.EXPENSE) else -raw

    def trial_balance(self) -> float:
        """Sum of all raw balances; zero when the books are consistent."""
        return round(sum(self._balances.values()), 2)

    def flows(self, week: int) -> dict[str, float]:
        """Natural-sign movement of each P&L account during one week."""
        out = {a: 0.0 for a, (k, _) in CHART.items()
               if k in (AccountType.REVENUE, AccountType.EXPENSE)}
        for entry in self.entries:
            if entry.week != week:
                continue
            for line in entry.lines:
                if line.account in out:
                    kind = CHART[line.account][0]
                    delta = line.debit - line.credit
                    out[line.account] += delta if kind == AccountType.EXPENSE else -delta
        return out
