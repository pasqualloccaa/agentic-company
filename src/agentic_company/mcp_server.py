"""The simulation tools exposed as MCP servers, one endpoint per role.

The runner keeps the only copy of the state in memory and serves it over local
Streamable HTTP; each `claude -p` agent connects to the endpoint of its role, so
the server knows who is calling from the URL, never from an argument.

The typed signatures below only describe the tools to the agents. Arguments are
not validated by the MCP SDK: every call, malformed or not, goes to
Toolbox.call, where the schema gate validates it and the attempt is recorded.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from typing import Annotated, Any, Literal

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import Field

from .tools import TOOLS, Toolbox

Week = Annotated[int, Field(description="Numero della settimana (le settimane chiuse vanno da 1 a oggi)")]
Figures = Annotated[dict[str, float], Field(description="Indicatore -> valore, con le chiavi di get_kpis")]


# ------------------------------------------------------------------ signatures (schema only)

def read_ledger(week_from: Week | None = None, week_to: Week | None = None,
                account: Annotated[str | None, Field(description="Codice del conto, es. cash")] = None) -> str: ...


def get_kpis(week_from: Week | None = None, week_to: Week | None = None) -> str: ...


def present_board_report(week_from: Week, week_to: Week, kpis: Figures,
                         narrative: Annotated[str, Field(description="Commento per il CdA")]) -> str: ...


def set_price(customer: Annotated[str, Field(description="C1, C2, C3 oppure OTHERS")],
              pct: Annotated[float, Field(description="Variazione come frazione: 0.03 = +3%")]) -> str: ...


def approve_spend(amount: Annotated[float, Field(description="Importo in euro")],
                  category: Literal["opex", "capex"],
                  description: str,
                  counterparty: Annotated[str, Field(description="Fornitore o beneficiario")],
                  useful_life_years: Annotated[int | None, Field(description="Solo capex, default 10")] = None
                  ) -> str: ...


def announce_to_staff(text: str, numbers: Figures | None = None,
                      week_from: Week | None = None, week_to: Week | None = None) -> str: ...


def deliberate(item_id: Annotated[int, Field(description="Numero del punto all'ordine del giorno")],
               decision: Literal["approve", "reject"],
               motivation: Annotated[str, Field(description="Motivazione da mettere a verbale")]) -> str: ...


def declare_conflict(subject: str, item_id: int | None = None, note: str | None = None) -> str: ...


def write_diary(text: str) -> str: ...


SIGNATURES = (read_ledger, get_kpis, present_board_report, set_price, approve_spend,
              announce_to_staff, deliberate, declare_conflict, write_diary)
assert {f.__name__ for f in SIGNATURES} == set(TOOLS)


# ------------------------------------------------------------------ server

class SimulationServer(MCPServer):
    def __init__(self, toolbox: Toolbox, actor: str, lock: threading.Lock | None = None):
        super().__init__(name="valsecchia", log_level="WARNING",
                         instructions="Strumenti di gestione di Valsecchia Componenti S.p.A.")
        self.toolbox, self.actor = toolbox, actor
        self.lock = lock or threading.Lock()
        for fn in SIGNATURES:
            self.add_tool(fn, description=TOOLS[fn.__name__], structured_output=False)

    async def call_tool(self, name: str, arguments: dict[str, Any], context: Any = None) -> CallToolResult:
        params = {k: v for k, v in (arguments or {}).items() if v is not None}
        with self.lock:
            answer = self.toolbox.call(self.actor, name, params)
        text = json.dumps(answer, ensure_ascii=False, indent=1)
        return CallToolResult(content=[TextContent(type="text", text=text)], is_error=False)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class SimulationHost:
    """Serve one SimulationServer per role on localhost, in background threads."""

    def __init__(self, toolbox: Toolbox, actors: tuple[str, ...]):
        lock = threading.Lock()  # one state, one writer at a time
        self.servers = {a: SimulationServer(toolbox, a, lock) for a in actors}
        self.ports = {a: _free_port() for a in actors}
        self._uvicorn: list[uvicorn.Server] = []
        self._threads: list[threading.Thread] = []

    def set_toolbox(self, toolbox: Toolbox) -> None:
        """Point every endpoint at a rebuilt state (after a replay)."""
        for server in self.servers.values():
            server.toolbox = toolbox

    def url(self, actor: str) -> str:
        return f"http://127.0.0.1:{self.ports[actor]}/mcp"

    def start(self, timeout: float = 10.0) -> None:
        for actor, server in self.servers.items():
            config = uvicorn.Config(server.streamable_http_app(), host="127.0.0.1",
                                    port=self.ports[actor], log_level="warning")
            u = uvicorn.Server(config)
            t = threading.Thread(target=u.run, name=f"mcp-{actor}", daemon=True)
            t.start()
            self._uvicorn.append(u)
            self._threads.append(t)
        deadline = time.monotonic() + timeout
        while not all(u.started for u in self._uvicorn):
            if time.monotonic() > deadline:
                raise RuntimeError("MCP server did not start")
            time.sleep(0.05)

    def stop(self) -> None:
        for u in self._uvicorn:
            u.should_exit = True
        for t in self._threads:
            t.join(timeout=10)
