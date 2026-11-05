"""Transport errors and retries, dynamic base URL, tool annotations, async start_e3d, audit log."""

import asyncio
import json

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

import audit
import e3d_launcher
import nexus_client
import server


def install_transport(monkeypatch, handler):
    """Route nexus_client through an httpx MockTransport."""
    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=5)
    monkeypatch.setattr(nexus_client, "_client", client)


OK = {"Success": True, "Data": {"x": 1}}


# ---- PY-5: transport errors ----------------------------------------------------------------

@pytest.mark.parametrize("error", [httpx.RemoteProtocolError("closed"), httpx.ReadError("reset"), httpx.WriteError("broken")])
def test_get_is_retried_once_after_a_dropped_connection(monkeypatch, error):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            raise error
        return httpx.Response(200, json=OK)

    install_transport(monkeypatch, handler)
    assert nexus_client.call("GET", "/api/Session/Info") == {"x": 1}
    assert len(calls) == 2


def test_read_only_post_is_retried(monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadError("reset")
        return httpx.Response(200, json=OK)

    install_transport(monkeypatch, handler)
    assert nexus_client.call("POST", "/api/Element/Query", body={"type": "SITE"}) == {"x": 1}


def test_second_failure_gives_a_clear_message(monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.RemoteProtocolError("closed")

    install_transport(monkeypatch, handler)
    with pytest.raises(nexus_client.NexusError, match="Connection to Nexus lost.*restarted"):
        nexus_client.call("GET", "/api/Session/Info")
    assert len(calls) == 2


@pytest.mark.parametrize("path", ["/api/PML/Run", "/api/PML/Execute"])
def test_pml_is_never_retried(monkeypatch, path):
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ReadError("reset")

    install_transport(monkeypatch, handler)
    with pytest.raises(nexus_client.NexusError, match="NOT retried"):
        nexus_client.call("POST", path, body={"script": "x"})
    assert len(calls) == 1


def test_batch_retry_depends_on_its_items():
    reads = {"requests": [{"method": "GET", "path": "/api/Health"}, {"method": "POST", "path": "/api/Element/Get"}]}
    writes = {"requests": [{"method": "POST", "path": "/api/PML/Run"}]}
    assert nexus_client.is_idempotent("POST", "/api/Batch", reads)
    assert not nexus_client.is_idempotent("POST", "/api/Batch", writes)
    assert not nexus_client.is_idempotent("POST", "/api/Batch", None)


def test_timeout_and_connect_errors_keep_their_messages(monkeypatch):
    install_transport(monkeypatch, lambda r: (_ for _ in ()).throw(httpx.ConnectError("no")))
    with pytest.raises(nexus_client.NexusError, match="Cannot reach"):
        nexus_client.call("GET", "/x")


# ---- PY-6: dynamic base URL ----------------------------------------------------------------

@pytest.fixture
def local_url(monkeypatch):
    monkeypatch.setattr(nexus_client, "NEXUS_URL", "http://localhost:5001")
    monkeypatch.setattr(nexus_client, "_active_url", None)
    monkeypatch.delenv("NEXUS_URL", raising=False)


def test_use_project_port_switches_the_port_on_the_same_host(local_url):
    assert nexus_client.base_url() == "http://localhost:5001"
    nexus_client.use_project_port(5007)
    assert nexus_client.base_url() == "http://localhost:5007"


def test_requests_follow_the_active_url(local_url, monkeypatch):
    seen = []
    install_transport(monkeypatch, lambda r: seen.append(str(r.url)) or httpx.Response(200, json=OK))
    nexus_client.use_project_port(5009)
    nexus_client.call("GET", "/api/Health")
    assert seen == ["http://localhost:5009/api/Health"]


def test_explicit_remote_nexus_url_is_not_overridden(monkeypatch):
    monkeypatch.setattr(nexus_client, "NEXUS_URL", "http://10.0.0.5:5001")
    monkeypatch.setattr(nexus_client, "_active_url", None)
    monkeypatch.setenv("NEXUS_URL", "http://10.0.0.5:5001")
    assert nexus_client.use_project_port(5007) is None
    assert nexus_client.base_url() == "http://10.0.0.5:5001"


def test_start_e3d_points_the_client_at_the_started_project(local_url, monkeypatch):
    monkeypatch.setattr(e3d_launcher, "start", lambda project, writable, on_wait=None: {"status": "started", "port": 5012})
    result = asyncio.run(server.start_e3d("DEMO"))
    assert result["status"] == "started"
    assert nexus_client.base_url() == "http://localhost:5012"


def test_start_e3d_already_running_also_switches(local_url, monkeypatch):
    monkeypatch.setattr(e3d_launcher, "start", lambda project, writable, on_wait=None: {"status": "already_running", "port": 5013})
    asyncio.run(server.start_e3d("DEMO"))
    assert nexus_client.base_url() == "http://localhost:5013"


# ---- PY-7: async start with progress -------------------------------------------------------

class FakeCtx:
    def __init__(self):
        self.infos, self.progress = [], []

    async def info(self, data, **kwargs):
        self.infos.append(data)

    async def report_progress(self, progress, total=None, message=None):
        self.progress.append((progress, total, message))


def test_start_e3d_is_async_runs_off_the_loop_and_reports_progress(local_url, monkeypatch):
    assert asyncio.iscoroutinefunction(server.start_e3d)

    def slow_start(project, writable, on_wait=None):
        on_wait(5, 120)
        return {"status": "started", "port": 5001}

    monkeypatch.setattr(e3d_launcher, "start", slow_start)
    ctx = FakeCtx()

    async def run():
        result = await server.start_e3d("DEMO", ctx=ctx)
        await asyncio.sleep(0.05)  # let the threadsafe progress coroutine finish
        return result

    asyncio.run(run())
    assert ctx.infos and ctx.progress == [(5, 120, "Waiting for Nexus (5 s)")]


# ---- PY-8: annotations ---------------------------------------------------------------------

READ_TOOLS = {"session_info", "query_elements", "count_elements", "batch", "resolve_names", "element_tree",
              "describe_element", "get_attributes", "list_children", "attribute_list", "list_pml", "e3d_projects"}
DESTRUCTIVE_TOOLS = {"save_work", "stop_e3d", "delete_pml", "run_pml", "pml_eval", "call_pml_function"}


def tools():
    return {t.name: t for t in server.mcp._tool_manager.list_tools()}


def test_every_tool_has_annotations():
    for name, tool in tools().items():
        assert tool.annotations is not None, name
        assert tool.annotations.open_world_hint is False, name


def test_read_tools_are_read_only():
    registered = tools()
    for name in READ_TOOLS:
        assert registered[name].annotations.read_only_hint is True, name


def test_destructive_tools_are_flagged_and_not_read_only():
    registered = tools()
    for name in DESTRUCTIVE_TOOLS:
        assert registered[name].annotations.destructive_hint is True, name
        assert not registered[name].annotations.read_only_hint, name


def test_every_tool_is_classified():
    assert set(tools()) >= READ_TOOLS | DESTRUCTIVE_TOOLS


# ---- PY-16: audit log ----------------------------------------------------------------------

def entries(tmp_path):
    path = tmp_path / "audit.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_audit_entry_has_hash_head_and_status(tmp_path):
    script = "x" * 500
    with audit.entry("run_pml", project="DEMO", mdb="900-TEST", script=script):
        pass
    [e] = entries(tmp_path)
    assert e["tool"] == "run_pml" and e["status"] == "ok" and e["project"] == "DEMO"
    assert len(e["script_sha256"]) == 64 and e["script_head"] == "x" * 200 and e["ts"]


def test_audit_records_failures_and_reraises(tmp_path):
    with pytest.raises(ToolError):
        with audit.entry("save_pml", script="y"):
            raise ToolError("nope")
    [e] = entries(tmp_path)
    assert e["status"] == "error" and "nope" in e["detail"]


def test_audit_log_is_append_only(tmp_path):
    for i in range(3):
        audit.record("stop_e3d", "ok", project=f"P{i}")
    assert [e["project"] for e in entries(tmp_path)] == ["P0", "P1", "P2"]


def test_save_pml_and_delete_pml_are_audited(tmp_path, monkeypatch):
    import pml_dev

    monkeypatch.setattr(pml_dev, "SANDBOX", tmp_path / "sb")
    server.save_pml("function", "mcpa", "define function !!mcpa()\nendfunction")
    server.delete_pml("function", "mcpa")
    assert [e["tool"] for e in entries(tmp_path)] == ["save_pml", "delete_pml"]


def test_unwritable_audit_path_never_breaks_the_call(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("NEXUS_AUDIT_LOG", str(blocker / "sub" / "audit.jsonl"))
    audit.record("stop_e3d", "ok")  # must not raise


def test_default_audit_location_is_outside_the_repo(monkeypatch):
    monkeypatch.delenv("NEXUS_AUDIT_LOG")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\x\AppData\Local")
    assert audit.log_path().parts[-2:] == ("NexusMCP", "audit.jsonl")
