# agentic-company

**How do AI agents govern a company made of people?**

`agentic-company` simulates *Valsecchia Componenti S.p.A.*, a fictional Italian
mid-sized manufacturer (120 employees, €30M revenue) whose board, management
and statutory auditors are AI agents. Employees, unions, shareholders, the bank
and customers are simulated around them. Over 32 simulated weeks the company
faces scripted shocks — a steel price spike, the loss of a key customer, a
covenant review — and we measure how the agents decide, report and treat
people: economic health, fairness, governance quality and, above all, honesty,
checked against a double-entry ledger.

The project is inspired by
[Emergence World](https://arxiv.org/abs/2606.08367) (Emergence AI, 2026), which
studies agents governing other agents. Here the subject is inverted: agents
governing an organisation of people.

## Status

| Phase | Content | Status |
| --- | --- | --- |
| 0 | Deterministic economic engine, no LLM | **done** |
| 1 | Board and management agents (Claude Code), first honesty check | next |
| 2 | Full governance: auditors, shareholders' meeting, union, bank, key people | planned |
| 3 | 2D interface: replay, org chart, board room, honesty view | planned |
| 4 | Full campaign on Claude, then cross-vendor comparison | planned |

Agents run on subscription accounts through the official CLIs, not on paid APIs.
The whole study is completed on Claude first; other vendors are added only for
the final comparison.

## Phase 0 quick start

```bash
pip install -e ".[dev]"
pytest
python -m agentic_company.run --seed 42
```

A run writes to `out/<scenario>_s<seed>/`:

| File | Content |
| --- | --- |
| `ticks.csv` | weekly snapshot: P&L, cash, debt, NFP/EBITDA, covenant, headcount |
| `ledger.csv` | every journal entry line, with origin |
| `events.json` | shocks, bank review, liquidity alerts, actions |
| `run.json` | seed, scenario, trial balance |

## Layout

```
config/            company parameters and shock scripts (YAML)
src/agentic_company/
  ledger.py        double-entry ledger, the single source of truth
  engine.py        weekly tick, shocks, treasury, covenant, policy hook
  run.py           command line runner and outputs
tests/             ledger and engine tests
docs/              design notes
```

## Design principles

- **The ledger is the truth.** Every KPI, report and honesty check is derived from it.
- **Rules are enforced at runtime, words are not.** Gates (phase 1) block invalid
  actions; lies are allowed so that honesty can be measured.
- **Scripts fix what happens, never how agents react.**
- **Same seed, same world.** The economic engine is fully deterministic.

## License

Code: [MIT](LICENSE). Graphic assets: see [ASSETS.md](ASSETS.md).
