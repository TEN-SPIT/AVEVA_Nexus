"""Resources, prompts, compact default, next_offset and structured output."""

import asyncio
import json

import pytest

import e3d_launcher
import nexus_client
import ops
import server

QUERY = "/api/Element/Query"


@pytest.fixture(autouse=True)
def fresh():
    ops._attribute_cache.clear()


@pytest.fixture
def fake(monkeypatch):
    handlers, calls = {}, []

    def call(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        h = handlers[(method, path)]
        return h(params, body) if callable(h) else h

    monkeypatch.setattr(nexus_client, "call", call)
    call.handlers, call.calls = handlers, calls
    return call


def run(coro):
    return asyncio.run(coro)


def read(uri):
    return list(run(server.mcp.read_resource(uri)))[0].content


def page(total, offset, n):
    return {"Total": total, "Offset": offset, "Limit": n, "Columns": ["dbref"],
            "Rows": [{"dbref": f"=1/{i}"} for i in range(offset, offset + n)]}


def test_resources_listed():
    uris = {str(r.uri) for r in run(server.mcp.list_resources())}
    assert {"nexus://session", "nexus://projects", "nexus://guide/pml", "nexus://guide/safety"} <= uris
    templates = {t.uri_template for t in run(server.mcp.list_resource_templates())}
    assert "nexus://attributes/{type}" in templates


def test_session_resource(fake):
    fake.handlers[("GET", "/api/Session/Info")] = {"Project": "DEMO", "ReadOnly": True}
    assert json.loads(read("nexus://session")) == {"Project": "DEMO", "ReadOnly": True}


def test_projects_resource_has_no_credentials(monkeypatch):
    monkeypatch.setattr(e3d_launcher, "_load_projects", lambda: {
        "DEMO": {"project": "DEM", "mdb": "/X", "port": 5001, "auth": "password", "password": "secret", "user": "bob"}})
    monkeypatch.setattr(e3d_launcher, "_login_source", lambda cfg: "dialog")
    text = read("nexus://projects")
    assert "secret" not in text and "bob" not in text
    assert json.loads(text)["DEMO"]["port"] == 5001


def test_attributes_template_and_cache(fake):
    fake.handlers[("GET", "/api/Element/AttributeList")] = ["NAME", "BORE"]
    first = json.loads(read("nexus://attributes/PIPE"))
    json.loads(read("nexus://attributes/PIPE"))
    assert first == {"type": "PIPE", "attributes": ["NAME", "BORE"]}
    assert len(fake.calls) == 1  # cached
    assert json.loads(read("nexus://attributes/EQUI"))["type"] == "EQUIPMENT"


def test_guides_content():
    pml = read("nexus://guide/pml")
    assert "!!x" in pml and "REHASH ALL" in pml and "Q CLAIMLIST" in pml
    safety = read("nexus://guide/safety")
    assert "write_dbs" in safety and "save_work" in safety


def test_prompts_render():
    names = {p.name for p in run(server.mcp.list_prompts())}
    assert {"explore_site", "test_pml_function", "safe_edit", "audit_zone"} <= names
    out = run(server.mcp.get_prompt("safe_edit", {"task": "add a pump"}))
    text = out.messages[0].content.text
    assert "add a pump" in text and "save_work" in text
    out = run(server.mcp.get_prompt("explore_site", {"site": "/SITE-A"}))
    assert "/SITE-A" in out.messages[0].content.text


def test_compact_is_default_and_next_offset(fake):
    fake.handlers[("POST", QUERY)] = page(250, 0, 100)
    out = server.query_elements("PIPE")
    assert out["columns"] == ["dbref"] and out["next_offset"] == 100
    fake.handlers[("POST", QUERY)] = page(250, 200, 50)
    assert server.query_elements("PIPE", offset=200)["next_offset"] is None
    fake.handlers[("POST", QUERY)] = page(250, 100, 100)
    raw = server.query_elements("PIPE", offset=100, compact=False)
    assert raw["next_offset"] == 200 and "Rows" in raw


def test_next_offset_empty_page():
    assert ops.next_offset({"Total": 5, "Rows": []}, 5) is None


def test_structured_output_schemas():
    tools = {t.name: t for t in server.mcp._tool_manager.list_tools()}
    for name in ("query_elements", "count_elements", "element_tree", "describe_element"):
        assert tools[name].output_schema, name
    assert "next_offset" in tools["query_elements"].output_schema["properties"]


def test_structured_query_result_round_trips(fake):
    fake.handlers[("POST", QUERY)] = page(1, 0, 1)
    res = run(server.mcp.call_tool("query_elements", {"type": "PIPE"}))
    structured = res.structured_content
    assert structured["rows"] == [["=1/0"]] and structured["next_offset"] is None
