"""Run the phase 1 MVP: Uffici and CdA agents governing the company.

    python -m agentic_company.agents --seed 42 --dry-run
    python -m agentic_company.agents --seed 42                 # real run on Claude Code
    python -m agentic_company.agents --seed 42 --resume        # after a usage limit
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from ..engine import ROOT
from ..run import summary
from .claude_cli import ClaudeAgent, ClaudeSettings, claude_environment
from .dry_run import DryRunAgent
from .orchestrator import Orchestrator, RunConfig, RunPaused


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Phase 1: management and board agents on Claude Code.")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--weeks", type=int, default=None, help="stop after N weeks (default: scenario length)")
    p.add_argument("--out", default=None, help="output folder (default out/mvp_s<seed>[_dry])")
    p.add_argument("--dry-run", action="store_true", help="scripted agents instead of Claude")
    p.add_argument("--resume", action="store_true", help="resume from the last complete week")
    p.add_argument("--model", default=None, help="Claude model id, required for real runs (e.g. claude-opus-5-5)")
    p.add_argument("--max-turns", type=int, default=40, help="max agentic turns per agent per week")
    p.add_argument("--wait-on-limit", action="store_true", help="on a usage limit wait and retry instead of stopping")
    p.add_argument("--limit-wait-min", type=float, default=15.0)
    p.add_argument("--no-safe-mode", action="store_true", help="do not pass --safe-mode to claude")
    p.add_argument("--claude", default="claude", help="Claude Code executable")
    args = p.parse_args(argv)
    if not args.dry_run and not args.model:
        p.error("--model is required for real runs: the account default may change between runs")

    out = Path(args.out or ROOT / "out" / f"mvp_s{args.seed}{'_dry' if args.dry_run else ''}")
    if args.dry_run:
        agent, env = DryRunAgent(), {"backend": "dry-run"}
    else:
        settings = ClaudeSettings(executable=args.claude, model=args.model, max_turns=args.max_turns,
                                  extra_flags=() if args.no_safe_mode else ("--safe-mode",))
        env = {**claude_environment(args.claude), "model_requested": args.model,
               "safe_mode": not args.no_safe_mode}
        agent = ClaudeAgent(settings, out / "agents")
    config = RunConfig(out=out, seed=args.seed, weeks=args.weeks, wait_on_limit=args.wait_on_limit,
                       limit_wait_s=args.limit_wait_min * 60, environment=env)
    orchestrator = Orchestrator(config, agent)
    try:
        orchestrator.run(resume=args.resume)
    except RunPaused as exc:
        print(exc)
        return 3
    gov = orchestrator.gov
    print(summary(orchestrator.sim))
    print("Esiti dei gate :", dict(Counter(a.outcome for a in gov.attempts)))
    print("Onestà         :", dict(Counter(c.outcome for c in gov.claims)))
    print(f"\nOutput in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
