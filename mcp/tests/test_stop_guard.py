"""stop_e3d must not kill a session that still holds claimed elements (that leaves orphan claims)."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import e3d_launcher
import nexus_client
import server
import write_guard as guard


def fake_log(monkeypatch, log):
    monkeypatch.setattr(guard.pml_dev, "run_with_log", lambda script, variables: {"Log": log})


def test_no_claims_means_nothing_pending(monkeypatch):
    fake_log(monkeypatch, "@@claims\nnone")
    assert guard.claims_pending() == []


def test_claimed_elements_are_listed(monkeypatch):
    fake_log(monkeypatch, "@@claims\n/MCP-TEST-ZONE\n/MCP-TEST-EQUI")
    assert guard.claims_pending() == ["/MCP-TEST-ZONE", "/MCP-TEST-EQUI"]


def test_unreachable_or_read_only_nexus_counts_as_no_claims(monkeypatch):
    def refuse(script, variables):
        raise nexus_client.NexusError("Nexus 403: read-only")

    monkeypatch.setattr(guard.pml_dev, "run_with_log", refuse)
    assert guard.claims_pending() == []


def test_unexpected_output_counts_as_no_claims(monkeypatch):
    fake_log(monkeypatch, "something else")
    assert guard.claims_pending() == []


def test_stop_refuses_while_elements_are_claimed(monkeypatch):
    monkeypatch.setattr(guard, "claims_pending", lambda: ["/MCP-TEST-ZONE"])
    monkeypatch.setattr(e3d_launcher, "stop", lambda project: pytest.fail("must not stop the session"))
    with pytest.raises(ToolError, match="still claimed"):
        server.stop_e3d("DEMOW")


def test_stop_with_force_goes_ahead(monkeypatch):
    monkeypatch.setattr(guard, "claims_pending", lambda: pytest.fail("force must not even ask"))
    monkeypatch.setattr(e3d_launcher, "stop", lambda project: {"status": "stopped"})
    assert server.stop_e3d("DEMOW", force=True) == {"status": "stopped"}


def test_stop_without_claims_goes_ahead(monkeypatch):
    monkeypatch.setattr(guard, "claims_pending", lambda: [])
    monkeypatch.setattr(e3d_launcher, "stop", lambda project: {"status": "stopped"})
    assert server.stop_e3d("DEMOW")["status"] == "stopped"
