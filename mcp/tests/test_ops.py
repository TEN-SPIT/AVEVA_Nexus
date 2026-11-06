"""New composite tools (batch, resolve, tree, describe, pml_eval, call_pml_function, query helpers),
with Nexus simulated. Each route-backed tool is tested with the new route and with an old Nexus (404)."""

import re

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import nexus_client
import ops
import server
import write_guard
from nexus_client import NexusError

ROUTE_MISSING = NexusError("Nexus 404: Route Not Found. Module 'X' not supported.", 404, True)


@pytest.fixture(autouse=True)
def fresh_state():
    ops._missing_routes.clear()
    ops._attribute_cache.clear()


@pytest.fixture
def nexus(monkeypatch):
    """Fake nexus_client.call: handlers maps (method, path) -> callable(params, body) or value/exception."""
    calls = []
    handlers = {}

    def fake_call(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        h = handlers.get((method, path), ROUTE_MISSING)
        if callable(h):
            h = h(params, body)
        if isinstance(h, Exception):
            raise h
        return h

    monkeypatch.setattr(nexus_client, "call", fake_call)
    fake_call.calls, fake_call.handlers = calls, handlers
    return fake_call


# --- batch ---

def test_batch_uses_the_route_when_available(nexus):
    nexus.handlers[("POST", "/api/Batch")] = [{"Status": 200, "Data": 1, "Error": None}]
    out = server.batch([{"method": "GET", "path": "/api/Session/Info"}])
    assert out == [{"Status": 200, "Data": 1, "Error": None}]
    assert nexus.calls[0][3] == {"requests": [{"method": "GET", "path": "/api/Session/Info"}]}


def test_batch_falls_back_to_sequential_calls_and_keeps_going_after_an_error(nexus):
    nexus.handlers[("GET", "/api/Session/Info")] = {"Project": "DEMO"}
    nexus.handlers[("GET", "/api/Element/Children")] = NexusError("Nexus 400: bad dbref", 400)
    out = server.batch([
        {"method": "GET", "path": "/api/Session/Info"},
        {"method": "GET", "path": "/api/Element/Children", "query": {"dbref": "x"}},
    ])
    assert out[0] == {"Status": 200, "Data": {"Project": "DEMO"}, "Error": None}
    assert out[1]["Status"] == 400 and "bad dbref" in out[1]["Error"]


def test_missing_route_is_remembered(nexus):
    server.batch([{"method": "GET", "path": "/api/Session/Info"}])
    server.batch([{"method": "GET", "path": "/api/Session/Info"}])
    assert sum(1 for c in nexus.calls if c[1] == "/api/Batch") == 1


def test_batch_refuses_pml_and_oversize(nexus):
    with pytest.raises(ToolError, match="read routes"):
        server.batch([{"method": "POST", "path": "/api/PML/Run", "body": {"script": "x"}}])
    with pytest.raises(ToolError, match="At most 50"):
        server.batch([{"path": "/api/Health"}] * 51)
    with pytest.raises(ToolError):
        server.batch([])


# --- resolve ---

def test_resolve_with_route(nexus):
    nexus.handlers[("POST", "/api/Element/Resolve")] = [{"Name": "/A", "Dbref": "=1/2", "Type": "SITE"}, None]
    assert server.resolve_names(["A", "/B"])[1] is None
    assert nexus.calls[0][3] == {"names": ["/A", "/B"]}


def test_resolve_fallback_uses_pml_lookups(nexus, monkeypatch):
    seen = []

    def run(script, variables=None):
        seen.append(script)
        return {"Ok": False, "Errors": ["Undefined name"], "Values": {},
                "Log": "MK0\n\nRef =1/2\n\nType SITE\nMK1"}

    monkeypatch.setattr(write_guard.pml_dev, "run_with_log", run)
    assert server.resolve_names(["/A", "/B"]) == [{"Name": "/A", "Dbref": "=1/2", "Type": "SITE"}, None]
    assert "Q REF OF /A" in seen[0]


def test_resolve_fallback_in_read_only_session_explains(nexus, monkeypatch):
    def run(script, variables=None):
        raise NexusError("Nexus 403: read-only", 403)

    monkeypatch.setattr(write_guard.pml_dev, "run_with_log", run)
    with pytest.raises(ToolError, match="read-only"):
        server.resolve_names(["/A"])


def test_resolve_rejects_odd_names(nexus):
    with pytest.raises(ToolError, match="Invalid element name"):
        server.resolve_names(["/A B"])
    with pytest.raises(ToolError, match="Invalid element name"):
        server.resolve_names(["/A\nSAVEWORK"])


# --- tree ---

def test_tree_with_route(nexus):
    nexus.handlers[("GET", "/api/Element/Tree")] = {"Root": "=1/1", "Items": [], "Truncated": False}
    server.element_tree("/S", depth=3, type="EQUI", limit=10)
    assert nexus.calls[0][2] == {"dbref": "/S", "depth": 3, "limit": 10, "type": "EQUIPMENT"}


def test_tree_fallback_walks_children(nexus):
    kids = {
        "=1/1": [{"dbref": "=1/2", "type": "ZONE", "name": "/Z"}],
        "=1/2": [{"dbref": "=1/3", "type": "EQUI", "name": "/E"}, {"dbref": "=1/4", "type": "PIPE", "name": "/P"}],
        "=1/3": [{"dbref": "=1/5", "type": "NOZZ", "name": "/N"}],
    }
    nexus.handlers[("GET", "/api/Element/Children")] = lambda p, b: {"Owner": "x", "Children": kids.get(p["dbref"], [])}
    tree = server.element_tree("=1/1", depth=2)
    assert [i["Dbref"] for i in tree["Items"]] == ["=1/2", "=1/3", "=1/4"]
    assert tree["Items"][1]["Depth"] == 2 and tree["Items"][1]["Owner"] == "=1/2"
    only = server.element_tree("=1/1", depth=3, type="EQUIPMENT")
    assert [i["Dbref"] for i in only["Items"]] == ["=1/3"]
    limited = server.element_tree("=1/1", depth=3, limit=2)
    assert len(limited["Items"]) == 2 and limited["Truncated"]


# --- describe ---

def test_describe_uses_valid_attributes_and_caches_them(nexus):
    nexus.handlers[("GET", "/api/Element/AttributeList")] = ["NAME", "DESC", "POS", "ORI"]

    def get(p, b):
        if b["attributes"] == ["TYPE", "NAME", "OWNER"]:
            return {"Rows": [{"dbref": "=1/2", "TYPE": "ZONE", "NAME": "/Z", "OWNER": "/S"}]}
        assert b["attributes"] == ["DESC", "POS", "ORI"]
        return {"Rows": [{"DESC": "unset", "POS": "E 0mm", "ORI": None}]}

    nexus.handlers[("POST", "/api/Element/Get")] = get
    nexus.handlers[("GET", "/api/Element/Children")] = {
        "Owner": "=1/1", "Children": [{"type": "EQUI"}, {"type": "EQUI"}, {"type": "PIPE"}]}
    info = server.describe_element("=1/2")
    assert info["type"] == "ZONE" and info["attributes"] == {"POS": "E 0mm"}
    assert info["children"] == {"total": 3, "by_type": {"EQUI": 2, "PIPE": 1}, "truncated": False}
    server.describe_element("=1/2")
    assert sum(1 for c in nexus.calls if c[1] == "/api/Element/AttributeList") == 1


def test_describe_resolves_names_and_reports_missing(nexus):
    nexus.handlers[("POST", "/api/Element/Resolve")] = [None]
    with pytest.raises(ToolError, match="not found"):
        server.describe_element("/NOPE")


# --- PML tools ---

@pytest.fixture
def pml(monkeypatch):
    runs = []
    state = {"values": {}, "ok": True, "raise": None}

    def guarded(script, variables):
        runs.append(("guarded", script))
        if state["raise"]:
            raise state["raise"]
        return {"Ok": state["ok"], "Errors": [] if state["ok"] else ["boom"], "Log": "",
                "Values": {v: state["values"].get(i) for i, v in enumerate(variables or [])}}

    monkeypatch.setattr(write_guard, "guarded_run", guarded)
    monkeypatch.setattr(write_guard.pml_dev, "run_with_log", lambda s, v=None: runs.append(("raw", s)) or {"Ok": True})
    return runs, state


def test_pml_eval_returns_values_in_order_and_cleans_up(pml):
    runs, state = pml
    state["values"] = {0: 3.0, 1: "ab"}
    out = server.pml_eval(["1 + 2", "|a| & |b|"])
    assert out == {"values": [3.0, "ab"], "ok": True}
    assert runs[0][0] == "guarded" and "= 1 + 2" in runs[0][1]
    assert runs[1][0] == "raw" and ".delete()" in runs[1][1]


def test_pml_eval_reports_errors(pml):
    runs, state = pml
    state["ok"] = False
    out = server.pml_eval(["nofunc()"])
    assert out["ok"] is False and out["errors"] == ["boom"]


def test_pml_eval_read_only_message(pml):
    runs, state = pml
    state["raise"] = NexusError("Nexus 403: read-only", 403)
    with pytest.raises(ToolError, match="writable session"):
        server.pml_eval(["1"])


@pytest.mark.parametrize("bad", ["1;2", "1\nSAVEWORK X", ""])
def test_pml_eval_rejects_multi_statement_input(pml, bad):
    with pytest.raises(ToolError):
        server.pml_eval([bad])
    assert pml[0] == []


def test_call_function_quotes_arguments(pml):
    runs, state = pml
    state["values"] = {0: "ok"}
    out = server.call_pml_function("!!mcpecho", ["hi there", 2, 1.5, True])
    assert re.fullmatch(r"!!mcpCall[0-9a-f]{6} = !!mcpecho\(\|hi there\|, 2, 1\.5, true\)", runs[0][1])
    assert out["value"] == "ok" and out["ok"]


def test_call_function_without_return(pml):
    runs, _ = pml
    server.call_pml_function("mcpdo", [], returns=False)
    assert runs[0][1] == "!!mcpdo()"
    assert len(runs) == 1  # nothing to clean up


@pytest.mark.parametrize("name", ["a b", "mcp.x", "x(1)", "!!x; SAVEWORK", "", "1abc"])
def test_call_function_validates_name(pml, name):
    with pytest.raises(ToolError, match="Invalid function name"):
        server.call_pml_function(name)


@pytest.mark.parametrize("arg", ["a|b", "a$b", "a\nb", [1], None, {"a": 1}])
def test_call_function_rejects_unsafe_arguments(pml, arg):
    with pytest.raises(ToolError):
        server.call_pml_function("mcpx", [arg])


# --- query / count ---

def test_query_name_like_and_equi_mapping(nexus):
    nexus.handlers[("POST", "/api/Element/Query")] = {"Total": 1, "Offset": 0, "Limit": 100,
                                                      "Columns": ["dbref", "NAME"],
                                                      "Rows": [{"dbref": "=1/2", "NAME": "/P1"}]}
    old = server.query_elements("EQUI", where="BORE gt 1", name_like="P*", compact=False)
    assert old["Rows"][0]["NAME"] == "/P1"  # old shape unchanged
    body = nexus.calls[0][3]
    assert body["type"] == "EQUIPMENT"
    assert body["where"] == "(BORE gt 1) AND MATCHWILD(NAME,|/P*|)"
    assert "scope" not in body


def test_query_compact_shape(nexus):
    nexus.handlers[("POST", "/api/Element/Query")] = {"Total": 1, "Offset": 0, "Limit": 100,
                                                      "Columns": ["dbref", "NAME"],
                                                      "Rows": [{"dbref": "=1/2", "NAME": "/P1"}]}
    out = server.query_elements("PIPE", name_like="*P*", compact=True)
    assert out == {"total": 1, "offset": 0, "columns": ["dbref", "NAME"], "rows": [["=1/2", "/P1"]], "next_offset": None}
    assert nexus.calls[0][3]["where"] == "MATCHWILD(NAME,|*P*|)"


def test_name_like_rejects_pipe(nexus):
    with pytest.raises(ToolError):
        server.query_elements("PIPE", name_like="a|b")


def test_count_elements(nexus):
    nexus.handlers[("POST", "/api/Element/Query")] = {"Total": 42, "Rows": [{"dbref": "=1/2"}]}
    assert server.count_elements("ZONE", scope="=1/1") == {"total": 42}
    assert nexus.calls[0][3] == {"type": "ZONE", "scope": "=1/1", "limit": 1}
