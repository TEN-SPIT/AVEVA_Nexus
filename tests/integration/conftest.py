"""Integration tests: they talk to a running Nexus (E3D must be up). Set NEXUS_URL / NEXUS_TOKEN if needed.

Markers:
  writable  - needs a session started with NEXUS_READONLY=0 (PML execution allowed)
  readonly  - needs a session started without it (the default)

Run both modes:
  deploy-local.ps1 -Target <dir> -Writable   ->  python -m pytest tests/integration -m "not readonly"
  deploy-local.ps1 -Target <dir>             ->  python -m pytest tests/integration -m "not writable"
"""
import json
import os
import urllib.error
import urllib.request

import pytest

BASE_URL = os.environ.get("NEXUS_URL", "http://localhost:5001").rstrip("/")
TOKEN = os.environ.get("NEXUS_TOKEN", "")


def pytest_configure(config):
    config.addinivalue_line("markers", "writable: needs a Nexus session started with NEXUS_READONLY=0")
    config.addinivalue_line("markers", "readonly: needs a Nexus session started in the default read-only mode")


def _request(method, path, body=None, timeout=60):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE_URL + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("X-Nexus-Token", TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read().decode("utf-8") or "{}")


def pytest_collection_modifyitems(config, items):
    try:
        status, _ = _request("GET", "/api/Health", timeout=3)
        reachable = status == 200
    except Exception:
        reachable = False
    if not reachable:
        skip = pytest.mark.skip(reason=f"Nexus not reachable at {BASE_URL}")
        for item in items:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def api():
    def call(method, path, body=None):
        return _request(method, path, body)
    return call


# The tests do not assume any particular project: they discover the elements they need in whatever project the
# running session has open, and skip when it has none.

@pytest.fixture(scope="session")
def site_with_zones(api):
    """A site that has at least one ZONE: {"dbref", "name", "zones": [child, ...]}."""
    _, body = api("POST", "/api/Element/Query", {"type": "SITE", "attributes": ["NAME"], "limit": 200})
    for row in body["Data"]["Rows"]:
        status, children = api("GET", f"/api/Element/Children?dbref={row['dbref']}&type=ZONE")
        if status == 200 and children["Data"]["Children"]:
            return {"dbref": row["dbref"], "name": row["NAME"], "zones": children["Data"]["Children"]}
    pytest.skip("the open project has no site with zones")


@pytest.fixture(scope="session")
def zone_with_pipes(api, site_with_zones):
    """The dbref of a ZONE that holds at least one PIPE."""
    _, body = api("POST", "/api/Element/Query", {"type": "ZONE", "limit": 200})
    candidates = [z["dbref"] for z in site_with_zones["zones"]] + [r["dbref"] for r in body["Data"]["Rows"]]
    for zone in candidates:
        status, result = api("POST", "/api/Element/Query", {"type": "PIPE", "scope": zone, "limit": 1})
        if status == 200 and result["Data"]["Total"] > 0:
            return zone
    pytest.skip("the open project has no zone with pipes")
