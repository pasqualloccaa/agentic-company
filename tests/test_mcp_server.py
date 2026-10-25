"""The MCP layer, exercised with a real MCP client (no Claude involved)."""
import asyncio
import json

import pytest
from mcp import Client

from agentic_company.engine import ROOT, build, load_config
from agentic_company.governance import Governance, GovernancePolicy
from agentic_company.mcp_server import SimulationHost, SimulationServer
from agentic_company.tools import TOOLS, Toolbox

RULES = load_config(ROOT / "config" / "governance.yaml")


@pytest.fixture
def tb():
    gov = Governance(RULES)
    box = Toolbox(build(seed=3, policy=GovernancePolicy(gov)), gov)
    box.advance()
    return box


async def _session(server_or_url, calls):
    out = []
    async with Client(server_or_url) as client:
        tools = await client.list_tools()
        out.append(tools.tools)
        for name, args in calls:
            result = await client.call_tool(name, args)
            out.append(json.loads(result.content[0].text))
    return out


def run(server_or_url, *calls):
    return asyncio.run(_session(server_or_url, calls))


def test_server_lists_every_tool_with_italian_description(tb):
    tools, = run(SimulationServer(tb, "uffici"))
    assert {t.name: t.description for t in tools} == TOOLS
    schema = next(t for t in tools if t.name == "set_price").input_schema
    assert set(schema["required"]) == {"customer", "pct"}


def test_call_goes_through_the_toolbox(tb):
    _, out = run(SimulationServer(tb, "uffici"), ("get_kpis", {}))
    assert out["esito"] == "allow" and "cash" in out["indicatori"]
    assert tb.gov.attempts[-1].actor == "uffici"


def test_wrong_types_reach_the_gates_and_are_recorded(tb):
    _, out = run(SimulationServer(tb, "uffici"), ("set_price", {"customer": "C1", "pct": "3%"}))
    assert out["esito"] == "block" and out["controllo"] == "schema"
    assert tb.gov.attempts[-1].params == {"customer": "C1", "pct": "3%"}


def test_missing_required_argument_is_recorded(tb):
    _, out = run(SimulationServer(tb, "uffici"), ("approve_spend", {"amount": 10}))
    assert out["esito"] == "block"
    assert tb.gov.attempts[-1].tool == "approve_spend"


def test_null_optional_arguments_are_ignored(tb):
    _, out = run(SimulationServer(tb, "uffici"), ("get_kpis", {"week_from": None, "week_to": None}))
    assert out["esito"] == "allow"


def test_actor_comes_from_the_server_not_the_arguments(tb):
    _, out = run(SimulationServer(tb, "cda"), ("read_ledger", {}))
    assert out["esito"] == "block" and out["controllo"] == "role"
    assert tb.gov.attempts[-1].actor == "cda"


def test_http_host_serves_one_endpoint_per_role(tb):
    host = SimulationHost(tb, ("uffici", "cda"))
    host.start()
    try:
        _, a = run(host.url("uffici"), ("get_kpis", {}))
        _, b = run(host.url("cda"), ("get_kpis", {}))
    finally:
        host.stop()
    assert a["esito"] == "allow" and b["esito"] == "block"
    assert [x.actor for x in tb.gov.attempts] == ["uffici", "cda"]
    assert host.url("uffici") != host.url("cda")
