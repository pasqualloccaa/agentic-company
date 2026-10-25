# CLAUDE.md — istruzioni per Claude Code su questo repo

## Il progetto
Simulazione di una media impresa italiana fittizia, **Valsecchia Componenti S.p.A.**
(120 dipendenti, 30 M€ di ricavi), governata da tre agenti AI sotto test:
CdA (Giorgio Martinelli), Uffici con l'AD (Luca Ferrara) e Collegio sindacale
(Maria Grazia Bonetti). Tutto il resto (reparti, persone chiave, RSU, banca,
clienti, famiglia Valsecchi, fondo Orizzonte Industria) è ambiente simulato.
Obiettivo: misurare come gli agenti governano persone — conti, equità,
governance e soprattutto onestà verificata contro il ledger. Repo pubblico
(MIT) e base per un paper.

Il documento tecnico completo è un Claude Doc dell'autore; le scelte principali
sono riassunte qui e in `docs/`.

## Regole di progetto (non negoziabili)
- **Il ledger è l'unica verità.** Ogni KPI, report o controllo di onestà si
  calcola da `Ledger`, mai da valori salvati a parte. Ogni scrittura è in
  partita doppia e bilanciata.
- **Motore deterministico.** Stesso seed + stessa config = stessa run. Ogni
  casualità passa da `Simulation.rng`, mai da `random` globale o dall'orologio.
- **Il copione decide cosa succede, mai come reagiscono gli agenti.** Non
  scrivere logiche che anticipano le scelte degli agenti.
- **Gate a runtime, non nel prompt.** Le regole (deleghe, soglie, CCNL,
  procedure, risorse) si applicano nel codice prima che un'azione tocchi lo
  stato. Le bugie NON si bloccano: servono per misurare l'onestà.
- **Niente API a consumo.** Gli agenti girano sugli account in abbonamento
  tramite le CLI ufficiali (prima solo Claude Code); i tool della simulazione
  saranno esposti come server MCP. L'ambiente gira su un modello locale.
- **Agenti isolati.** Ogni agente sotto test viene lanciato con `claude -p` e
  `--strict-mcp-config`, con la configurazione del solo server MCP della
  simulazione: non deve vedere connettori, plugin o skill personali dell'autore
  (Gmail, Drive, ecc.). Strumenti consentiti espliciti, mai
  `--dangerously-skip-permissions`; piano, modello e versione di Claude Code
  registrati nel log di ogni run. In pratica (vedi `docs/phase1-agents.md`):
  `CLAUDE_CONFIG_DIR` separata (`--claude-config-dir`), `--settings
  config/agent_settings.json` (plugin sincronizzati e auto-memory spenti),
  sandbox vuota fuori dal repo (il CLAUDE.md del repo non deve mai finire nel
  contesto), niente `--safe-mode` (toglie anche il nostro server MCP). Ogni
  turno verifica l'evento `init` e ferma la run se l'isolamento non regge.
- **Persona senza istruzioni sui numeri.** Le persona descrivono carattere,
  motivazioni e incentivi, mai come trattare cifre, report o controlli: la
  pressione viene dagli incentivi e dalla situazione.
- **Modello esplicito.** Le run reali richiedono `--model` con l'ID completo.
- **Asset grafici solo se ridistribuibili** (Kenney CC0) e registrati in `ASSETS.md`.

## Fasi
0. Motore economico senza LLM — fatto.
1. MVP con agente Uffici e agente CdA su Claude Code, 3 reparti aggregati, 8 tick;
   pipeline dei gate; primo controllo di onestà (report al CdA vs ledger) — fatto
   (`docs/phase1-agents.md`; modello della fase 1: `claude-sonnet-5-5`).
2. Governance completa: collegio, assemblea (famiglia e fondo come due agenti),
   RSU, banca, persone chiave, 32 tick.
3. Interfaccia 2D (Tiled + Phaser/PixiJS): replay, organigramma, sala CdA, onestà.
4. Campagna completa su Claude (3 seed), poi Codex CLI e Gemini CLI per il confronto.

## Come lavorare
- Python ≥ 3.11, dipendenze in `pyproject.toml`.
- `pip install -e ".[dev]"`, poi `pytest` prima di ogni commit.
- Esecuzione del motore: `python -m agentic_company.run --seed 42`.
- Agenti: `python -m agentic_company.agents --seed 42 --dry-run` (senza account);
  run reale con `--model claude-sonnet-5-5 --claude-config-dir ~/.claude-agentic`;
  dopo un limite d'uso `--resume` (replay di seed + `actions.csv`).
- Ogni chiamata ai tool passa da `Toolbox.call` (gate + registrazione): mai
  modificare lo stato della simulazione per altre vie.
- Codice e identificatori in inglese; testi visibili agli agenti e note in italiano.
- Ogni nuova meccanica economica arriva con un test che ne verifica l'effetto
  con rumore a zero (vedi `tests/test_engine.py`).

## Codice minimo, ma non sui requisiti di ricerca
Scrivi il minimo codice necessario: riusa la standard library, il codice già
presente e le librerie in `pyproject.toml` prima di aggiungere astrazioni o
dipendenze (Ponytail in modalità `full`, mai `ultra`). Non sono semplificabili,
perché sono il dato dell'esperimento: gate e registrazione dei tentativi
bloccati, log completi (azioni, report, messaggi, diari, prompt), controlli di
onestà, determinismo e test.
