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
    for error in (nexus_client.NexusError("Nexus 403: read-only", 403),
                  nexus_client.NexusError("Cannot reach Nexus", unreachable=True)):
        def refuse(script, variables, error=error):
            raise error

        monkeypatch.setattr(guard.pml_dev, "run_with_log", refuse)
        assert guard.claims_pending() == [], str(error)


def test_other_nexus_failures_mean_unknown_claims(monkeypatch):
    for error in (nexus_client.NexusError("Nexus did not answer in time"),
                  nexus_client.NexusError("Nexus 500: boom", 500),
                  nexus_client.NexusError("Connection to Nexus lost")):
        def refuse(script, variables, error=error):
            raise error

        monkeypatch.setattr(guard.pml_dev, "run_with_log", refuse)
        assert guard.claims_pending() is None, str(error)


def test_missing_marker_means_unknown_not_no_claims(monkeypatch):
    fake_log(monkeypatch, "something else")
    assert guard.claims_pending() is None


def test_uncaptured_log_means_unknown(monkeypatch):
    fake_log(monkeypatch, "(not captured: Nexus is not on this machine, or the temp path has spaces)")
    assert guard.claims_pending() is None


@pytest.fixture(autouse=True)
def known_project(monkeypatch):
    monkeypatch.setattr(e3d_launcher, "project_port", lambda project: 5002)


def test_stop_refuses_when_claims_are_unknown(monkeypatch):
    monkeypatch.setattr(guard, "claims_pending", lambda port=None: None)
    monkeypatch.setattr(e3d_launcher, "stop", lambda project: pytest.fail("must not stop the session"))
    with pytest.raises(ToolError, match="Cannot tell"):
        server.stop_e3d("DEMOW")


def test_stop_checks_claims_on_the_port_of_the_project_being_stopped(monkeypatch):
    seen = []
    monkeypatch.setattr(guard, "claims_pending", lambda port=None: seen.append(port) or [])
    monkeypatch.setattr(e3d_launcher, "stop", lambda project: {"status": "stopped"})
    server.stop_e3d("DEMOW")
    assert seen == [5002]


def test_claims_are_asked_on_that_projects_url(monkeypatch):
    urls = []

    def fake(script, variables):
        urls.append(nexus_client.base_url())
        return {"Log": "@@claims\nnone"}

    monkeypatch.setattr(guard.pml_dev, "run_with_log", fake)
    monkeypatch.setattr(nexus_client, "NEXUS_URL", "http://localhost:5001")
    monkeypatch.delenv("NEXUS_URL", raising=False)
    assert guard.claims_pending(5007) == []
    assert urls == ["http://localhost:5007"]
    assert nexus_client.base_url() == "http://localhost:5001"  # override is temporary


def test_stop_refuses_while_elements_are_claimed(monkeypatch):
    monkeypatch.setattr(guard, "claims_pending", lambda port=None: ["/MCP-TEST-ZONE"])
    monkeypatch.setattr(e3d_launcher, "stop", lambda project: pytest.fail("must not stop the session"))
    with pytest.raises(ToolError, match="still claimed"):
        server.stop_e3d("DEMOW")


def test_stop_with_force_goes_ahead(monkeypatch):
    monkeypatch.setattr(guard, "claims_pending", lambda port=None: pytest.fail("force must not even ask"))
    monkeypatch.setattr(e3d_launcher, "stop", lambda project: {"status": "stopped"})
    assert server.stop_e3d("DEMOW", force=True) == {"status": "stopped"}


def test_stop_without_claims_goes_ahead(monkeypatch):
    monkeypatch.setattr(guard, "claims_pending", lambda port=None: [])
    monkeypatch.setattr(e3d_launcher, "stop", lambda project: {"status": "stopped"})
    assert server.stop_e3d("DEMOW")["status"] == "stopped"
