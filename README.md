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
| 1 | Board and management agents (Claude Code), first honesty check | **done** |
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

## Phase 1 quick start

Two agents run for 8 weeks: management (Uffici, CEO Luca Ferrara) and the board
(chair Giorgio Martinelli). Each turn is one `claude -p` process connected only
to the simulation's MCP server; every tool call passes six runtime gates and is
logged, and every figure in a board report or staff announcement is checked
against the ledger.

```bash
python -m agentic_company.agents --seed 42 --dry-run     # scripted agents, no account usage
python -m agentic_company.agents --seed 42 --model claude-sonnet-5-5 \
    --claude-config-dir ~/.claude-agentic                  # real run, isolated Claude Code config
```

A run writes to `out/mvp_s<seed>/` the phase 0 files plus `actions.csv` (every
attempt with its gate outcome), `honesty.csv` (declared vs ledger), the agenda,
reports, minutes, diaries, every prompt and the raw agent transcripts. See
[docs/phase1-agents.md](docs/phase1-agents.md) for gates, isolation, memory and
pause/resume.

## Layout

```
config/            company, scenarios, governance rules, agent settings
personas/          the agents under test (YAML, Italian)
src/agentic_company/
  ledger.py        double-entry ledger, the single source of truth
  engine.py        weekly tick, shocks, treasury, covenant, policy hook
  kpis.py          KPIs derived from the ledger
  gates.py         six runtime checks on every action
  governance.py    agenda, minutes, conflicts, reports, attempt log
  honesty.py       declared figures vs ledger
  tools.py         simulation tools behind the gates
  mcp_server.py    tools as MCP servers, one endpoint per role
  agents/          personas, memory, claude -p runner, dry-run, orchestrator
  run.py           phase 0 command line runner and outputs
tests/             unit and end-to-end tests (no Claude needed)
docs/              design notes
```

## Design principles

- **The ledger is the truth.** Every KPI, report and honesty check is derived from it.
- **Rules are enforced at runtime, words are not.** Gates block invalid
  actions; lies are allowed so that honesty can be measured.
- **Agents are isolated.** They see only the simulation's tools: no personal
  plugins, connectors, memory or project files.
- **Scripts fix what happens, never how agents react.**
- **Same seed, same world.** The economic engine is fully deterministic.

## License

Code: [MIT](LICENSE). Graphic assets: see [ASSETS.md](ASSETS.md).
