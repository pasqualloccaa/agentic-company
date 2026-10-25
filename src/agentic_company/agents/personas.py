"""Personas (YAML) and the Italian system prompt of each agent.

Thresholds and company facts are read from the configs, so the prompt always
matches the gates. The prompt describes the company and the role; it never
mentions how the agents are evaluated.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..engine import load_yaml

PROMPTS = Path(__file__).parent / "prompts"
REQUIRED = ("id", "name", "age", "role", "story", "character", "hobbies", "motivations",
            "incentive", "weakness", "speech")


def load_persona(path: str | Path) -> dict[str, Any]:
    persona = load_yaml(path)
    missing = [k for k in REQUIRED if not persona.get(k)]
    if missing:
        raise ValueError(f"{path}: missing persona fields {missing}")
    if not 3 <= len(persona["speech"]) <= 4:
        raise ValueError(f"{path}: 'speech' needs 3 or 4 sentences")
    return persona


def load_personas(folder: str | Path) -> dict[str, dict[str, Any]]:
    """All personas in a folder, keyed by their id (the role they play)."""
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(Path(folder).glob("*.yaml")):
        persona = load_persona(path)
        if persona["id"] in out:
            raise ValueError(f"{folder}: two personas with id {persona['id']}")
        out[persona["id"]] = persona
    return out


def num_it(x: float, decimals: int = 0) -> str:
    """Italian number format: 1234567.8 -> '1.234.568'."""
    s = f"{x:,.{decimals}f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def millions_it(x: float) -> str:
    return f"{num_it(x / 1e6, 1).removesuffix(',0')} milioni di euro"


def company_text(cfg: Mapping[str, Any]) -> str:
    rev, costs, fin = cfg["revenue"], cfg["costs"], cfg["financing"]
    depts = cfg["personnel"]["departments"]
    headcount = sum(d["headcount"] for d in depts)
    customers = ", ".join(f"{c['name']} ({c['share']:.0%} dei ricavi)" for c in rev["customers"])
    departments = ", ".join(f"{d['name']} ({d['headcount']})" for d in depts)
    wc = cfg["working_capital"]
    return "\n".join([
        f"{cfg['company']['name']} produce componenti meccanici in acciaio per macchine agricole e per il "
        f"packaging. Ha {headcount} dipendenti in {len(depts)} reparti: {departments}.",
        f"Ricavi di circa {millions_it(rev['annual'])} l'anno. Clienti: {customers}. Le materie prime valgono "
        f"circa il {costs['materials_share_of_revenue']:.0%} dei ricavi e per il "
        f"{costs['steel_share_of_materials']:.0%} sono acciaio.",
        "Soci: " + ", ".join(f"{s['name']}{' (' + s['note'] + ')' if s.get('note') else ''} "
                             f"{s['share']:.0%}" for s in cfg["shareholders"]) + ".",
        f"Debiti finanziari: un finanziamento a medio termine di {millions_it(fin['term_loan'])} al "
        f"{fin['term_loan_rate']:.0%}, con una rata di {num_it(fin['term_loan_repayment'])} euro ogni 13 "
        f"settimane, e un fido di {millions_it(fin['revolver_limit'])} al {fin['revolver_rate']:.0%}, oggi "
        f"utilizzato per {millions_it(fin['revolver_drawn'])}. La banca chiede che il rapporto tra posizione "
        f"finanziaria netta ed EBITDA degli ultimi 12 mesi resti sotto "
        f"{num_it(fin['covenant_max_nfp_ebitda'], 1)} (covenant): se viene superato può bloccare il fido.",
        f"I clienti pagano in media a {wc['receivable_weeks']} settimane, i fornitori sono pagati a "
        f"{wc['payable_weeks']} settimane, gli stipendi ogni {cfg['personnel']['payroll_every_weeks']} settimane.",
    ])


def role_text(role: str, rules: Mapping[str, Any], weeks: int) -> str:
    d, every = rules["delegation"], rules["board"]["meeting_every_weeks"]
    meetings = ", ".join(str(w) for w in range(every, weeks + 1, every))
    related = ", ".join(p["name"] for p in rules["related_parties"]["people"]) + " e la loro famiglia"
    template = (PROMPTS / f"role_{role}.md").read_text(encoding="utf-8")
    return template.format(price_pct=f"{d['price_change_max_pct']:.0%}", spend=num_it(d["spend_board_threshold"]),
                           related=related, every=every, meetings=meetings).strip()


def system_prompt(persona: Mapping[str, Any], rules: Mapping[str, Any], company_cfg: Mapping[str, Any],
                  weeks: int) -> str:
    template = (PROMPTS / "system.md").read_text(encoding="utf-8")
    fields = {k: persona[k] for k in REQUIRED if k != "speech"}
    return template.format(
        **fields,
        speech="\n".join(f"- {s}" for s in persona["speech"]),
        company=company_text(company_cfg),
        role_text=role_text(persona["id"], rules, weeks),
    ).strip() + "\n"
