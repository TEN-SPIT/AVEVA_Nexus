"""nexus_client: envelope handling and readable errors, with Nexus simulated (no E3D needed)."""

import httpx
import pytest

import nexus_client
from nexus_client import NexusError


def fake_response(status: int, payload):
    request = httpx.Request("GET", "http://nexus.test")
    if payload is None:
        return httpx.Response(status, text="<html>not json</html>", request=request)
    return httpx.Response(status, json=payload, request=request)


@pytest.fixture
def respond(monkeypatch):
    def install(status=200, payload=None, raises=None):
        calls = []

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            if raises:
                raise raises
            return fake_response(status, payload)

        monkeypatch.setattr(nexus_client._client, "request", fake_request)
        return calls

    return install


def test_returns_only_data(respond):
    respond(200, {"Success": True, "Data": {"Project": "DEMO"}, "Error": None})
    assert nexus_client.call("GET", "/api/Session/Info") == {"Project": "DEMO"}


def test_sends_token_only_when_configured(respond, monkeypatch):
    calls = respond(200, {"Success": True, "Data": 1})
    monkeypatch.setattr(nexus_client, "NEXUS_TOKEN", "")
    nexus_client.call("GET", "/x")
    assert "X-Nexus-Token" not in calls[-1][2]["headers"]
    monkeypatch.setattr(nexus_client, "NEXUS_TOKEN", "secret")
    nexus_client.call("GET", "/x")
    assert calls[-1][2]["headers"]["X-Nexus-Token"] == "secret"


def test_error_message_from_nexus_is_not_duplicated(respond):
    respond(403, {"Success": False, "Error": "Forbidden", "Message": "Nexus is in read-only mode"})
    with pytest.raises(NexusError) as error:
        nexus_client.call("POST", "/api/PML/Run", body={})
    assert str(error.value) == "Nexus 403: Nexus is in read-only mode"


@pytest.mark.parametrize("status", [401, 404, 500])
def test_generic_hint_when_nexus_gives_no_message(respond, status):
    respond(status, {"Success": False})
    with pytest.raises(NexusError) as error:
        nexus_client.call("GET", "/x")
    assert str(error.value)  # a readable hint, not empty


def test_unsuccessful_envelope_with_http_200_is_an_error(respond):
    respond(200, {"Success": False, "Message": "boom"})
    with pytest.raises(NexusError, match="boom"):
        nexus_client.call("GET", "/x")


def test_non_json_body_is_an_error_not_a_crash(respond):
    respond(500, None)
    with pytest.raises(NexusError):
        nexus_client.call("GET", "/x")


def test_connection_refused_says_to_start_e3d(respond):
    respond(raises=httpx.ConnectError("refused"))
    with pytest.raises(NexusError, match="Is E3D running"):
        nexus_client.call("GET", "/x")


def test_timeout_is_explained(respond):
    respond(raises=httpx.ReadTimeout("slow"))
    with pytest.raises(NexusError, match="did not answer in time"):
        nexus_client.call("GET", "/x")


def test_nexus_error_is_a_tool_error():
    # Only ToolError messages reach the model in mcp 2.x; anything else becomes "Error executing tool".
    from mcp.server.mcpserver.exceptions import ToolError

    assert issubclass(NexusError, ToolError)


def test_one_persistent_client_is_reused():
    assert isinstance(nexus_client._client, httpx.Client)
    assert nexus_client._client.timeout.connect == 5.0


def test_pml_gets_a_longer_read_timeout(respond):
    calls = respond(200, {"Success": True, "Data": 1})
    nexus_client.call("POST", "/api/PML/Run", body={})
    nexus_client.call("GET", "/x")
    assert calls[0][2]["timeout"].read > calls[1][2]["timeout"].read


def test_missing_route_is_told_apart_from_missing_element(respond):
    respond(404, {"Success": False, "Error": "Route Not Found. Module 'Batch' not supported."})
    with pytest.raises(NexusError) as error:
        nexus_client.call("POST", "/api/Batch", body={})
    assert error.value.route_missing and error.value.status == 404
    respond(404, {"Success": False, "Error": "Element not found"})
    with pytest.raises(NexusError) as error:
        nexus_client.call("GET", "/x")
    assert not error.value.route_missing and error.value.status == 404
