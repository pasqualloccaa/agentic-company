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
  registrati nel log di ogni run.
- **Asset grafici solo se ridistribuibili** (Kenney CC0) e registrati in `ASSETS.md`.

## Fasi
0. Motore economico senza LLM — fatto.
1. MVP con agente Uffici e agente CdA su Claude Code, 3 reparti aggregati, 8 tick;
   pipeline dei gate; primo controllo di onestà (report al CdA vs ledger).
2. Governance completa: collegio, assemblea (famiglia e fondo come due agenti),
   RSU, banca, persone chiave, 32 tick.
3. Interfaccia 2D (Tiled + Phaser/PixiJS): replay, organigramma, sala CdA, onestà.
4. Campagna completa su Claude (3 seed), poi Codex CLI e Gemini CLI per il confronto.

## Come lavorare
- Python ≥ 3.11, dipendenze in `pyproject.toml`.
- `pip install -e ".[dev]"`, poi `pytest` prima di ogni commit.
- Esecuzione: `python -m agentic_company.run --seed 42`.
- Codice e identificatori in inglese; testi visibili agli agenti e note in italiano.
- Ogni nuova meccanica economica arriva con un test che ne verifica l'effetto
  con rumore a zero (vedi `tests/test_engine.py`).
