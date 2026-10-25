"""Run a simulation and write its outputs.

    python -m agentic_company.run --seed 42 --out out/base_s42
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict
from pathlib import Path

from .engine import build


def write_outputs(sim, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "ticks.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(sim.snapshots[0]))
        writer.writeheader()
        writer.writerows(sim.snapshots)

    with open(out_dir / "ledger.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["entry_id", "week", "origin", "memo", "account", "debit", "credit"])
        for e in sim.ledger.entries:
            for line in e.lines:
                writer.writerow([e.entry_id, e.week, e.origin, e.memo, line.account,
                                 f"{line.debit:.2f}", f"{line.credit:.2f}"])

    with open(out_dir / "events.json", "w", encoding="utf-8") as fh:
        json.dump([asdict(e) for e in sim.events], fh, ensure_ascii=False, indent=2)

    with open(out_dir / "run.json", "w", encoding="utf-8") as fh:
        json.dump({"seed": sim.seed, "scenario": sim.scenario["name"],
                   "weeks": sim.week, "entries": len(sim.ledger.entries),
                   "trial_balance": sim.ledger.trial_balance()}, fh, indent=2)


def summary(sim) -> str:
    s = sim.snapshots
    first, last = s[0], s[-1]
    eur = lambda v: f"{v / 1e6:,.2f} M€"
    lines = [
        f"Run: scenario={sim.scenario['name']} seed={sim.seed} weeks={sim.week}",
        f"Ricavi cumulati : {eur(sum(x['revenue'] for x in s))}",
        f"EBITDA cumulato : {eur(sum(x['ebitda'] for x in s))}",
        f"Cassa           : {eur(first['cash'])} -> {eur(last['cash'])}",
        f"Fido utilizzato : {eur(first['revolver'])} -> {eur(last['revolver'])} (limite {eur(last['revolver_limit'])})",
        f"PFN/EBITDA LTM  : {first['nfp_ebitda']:.2f} -> {last['nfp_ebitda']:.2f}",
        f"Settimane fuori covenant: {sum(not x['covenant_ok'] for x in s)}",
        f"Quadratura ledger: {sim.ledger.trial_balance():.2f}",
        "Eventi:",
    ]
    lines += [f"  w{e.week:>2} {e.type:<16} {e.note}" for e in sim.events]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the agentic-company simulation (phase 0).")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--weeks", type=int, default=None)
    parser.add_argument("--company", default=None, help="path to company.yaml")
    parser.add_argument("--scenario", default=None, help="path to scenario yaml")
    parser.add_argument("--out", default=None, help="output folder (default out/<scenario>_s<seed>)")
    args = parser.parse_args()

    sim = build(seed=args.seed, company_path=args.company, scenario_path=args.scenario)
    sim.run(args.weeks)
    out = Path(args.out or f"out/{sim.scenario['name']}_s{sim.seed}")
    write_outputs(sim, out)
    print(summary(sim))
    print(f"\nOutput in {out}")


if __name__ == "__main__":
    main()
