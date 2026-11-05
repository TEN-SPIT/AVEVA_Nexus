"""Thin HTTP client for the Nexus add-in running inside E3D.

Every Nexus response is an envelope: {"Success", "Data", "Error", "Message", "RequestId"}.
This module sends the request, opens the envelope and returns only `Data`.
Any failure becomes a NexusError with a short message the model can act on.
"""

import contextlib
import logging
import os
from typing import Any
from urllib.parse import urlparse

import httpx
from mcp.server.mcpserver.exceptions import ToolError

NEXUS_URL = os.environ.get("NEXUS_URL", "http://localhost:5001")
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
_active_url: str | None = None  # set by use_project_port after start_e3d
NEXUS_TOKEN = os.environ.get("NEXUS_TOKEN", "")  # empty for the local (no-token) setup

# What each HTTP status means for the caller; used when Nexus gives no better message.
_STATUS_HINTS = {
    400: "Nexus rejected the request (bad input or the PML failed).",
    401: "Nexus rejected the token. Check NEXUS_TOKEN.",
    403: "Nexus is in read-only mode (or the request came from a browser origin).",
    404: "Not found: the route, variable or element does not exist.",
    405: "Method not allowed for this Nexus route.",
    500: "Nexus had an internal error.",
}


class NexusError(ToolError):
    """A readable failure talking to Nexus.

    Subclassing ToolError matters: the MCP SDK shows the model the message of a
    ToolError, but hides the message of any other exception ("Error executing tool").

    `status` is the HTTP status (None if there was none) and `route_missing` is True when Nexus
    answered 404 because the route itself does not exist (an older Nexus DLL), as opposed to a
    missing element or variable.
    """

    def __init__(self, message: str, status: int | None = None, route_missing: bool = False,
                 unreachable: bool = False):
        super().__init__(message)
        self.status = status
        self.route_missing = route_missing
        self.unreachable = unreachable   # nothing listens there (connection refused): no session at all


# One keep-alive connection pool for the whole server process: short connect timeout (Nexus is
# local, so an unreachable one should fail fast), long read timeout (PML and big queries can be slow).
# Longer than Nexus's worst gate wait (busy 20 s + UI start 30 s + stuck 120 s), so its explanatory 503 arrives.
_TIMEOUT = httpx.Timeout(180.0, connect=5.0)
_PML_TIMEOUT = httpx.Timeout(190.0, connect=5.0)
_client = httpx.Client(timeout=_TIMEOUT)
logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per request is just noise on stderr


def base_url() -> str:
    """URL requests go to: the one set after start_e3d, else NEXUS_URL."""
    return _active_url or NEXUS_URL


def url_for_port(port: int) -> str | None:
    """URL of the Nexus of a project on `port`, same host as NEXUS_URL; None if NEXUS_URL was set
    explicitly to a non-local host (a remote Nexus), where the port of a local project means nothing."""
    parsed = urlparse(NEXUS_URL)
    if "NEXUS_URL" in os.environ and parsed.hostname not in _LOCAL_HOSTS:
        return None
    host = f"[{parsed.hostname}]" if ":" in (parsed.hostname or "") else (parsed.hostname or "localhost")
    return f"{parsed.scheme or 'http'}://{host}:{int(port)}"


def use_project_port(port: int) -> str | None:
    """Point the client at the Nexus of a project started with start_e3d (same host, that port).
    Returns the new URL, or None when it was left alone (see url_for_port)."""
    global _active_url
    url = url_for_port(port)
    if url:
        _active_url = url
    return url


@contextlib.contextmanager
def override_url(url: str | None):
    """Temporarily send requests to `url` (None: no change). Not for concurrent use."""
    global _active_url
    previous = _active_url
    if url:
        _active_url = url
    try:
        yield
    finally:
        _active_url = previous


# Routes that only read: safe to repeat after a dropped connection. PML/Run and PML/Execute are
# never repeated (the script may have run before the connection broke).
_READ_ROUTES = {
    "/api/Health", "/api/Session/Info", "/api/Element/Query", "/api/Element/Get", "/api/Element/Children",
    "/api/Element/AttributeList", "/api/Element/Resolve", "/api/Element/Tree",
}


def is_idempotent(method: str, path: str, body: dict | None = None) -> bool:
    if method.upper() == "GET":
        return True
    if path in _READ_ROUTES:
        return True
    if path == "/api/Batch":
        items = (body or {}).get("requests")
        return isinstance(items, list) and bool(items) and all(
            isinstance(i, dict) and i.get("path") in _READ_ROUTES
            for i in items
        )
    return False


def call(method: str, path: str, *, params: dict | None = None, body: dict | None = None) -> Any:
    """Call a Nexus route and return the envelope's `Data`.

    A dropped connection is retried once for reads (GET and read-only POST routes); never for PML.
    """
    headers = {"X-Nexus-Token": NEXUS_TOKEN} if NEXUS_TOKEN else {}
    url = base_url()
    attempts = 2 if is_idempotent(method, path, body) else 1
    for attempt in range(attempts):
        try:
            response = _client.request(
                method,
                f"{url}{path}",
                params=params,
                json=body,
                headers=headers,
                timeout=_PML_TIMEOUT if path.startswith("/api/PML") else _TIMEOUT,
            )
            break
        except httpx.ConnectError:
            raise NexusError(f"Cannot reach Nexus at {url}. Is E3D running with Nexus started?", unreachable=True) from None
        except httpx.TimeoutException:
            raise NexusError(
                "Nexus did not answer in time (E3D may be busy with another request)."
                + (" The script may still have run; check the state before repeating it." if path.startswith("/api/PML") else "")
            ) from None
        except httpx.TransportError as error:
            if attempt + 1 < attempts:
                continue
            raise NexusError(
                f"Connection to Nexus lost ({type(error).__name__}); E3D may have restarted or crashed. "
                + ("" if attempts > 1 else "The request was NOT retried because it may have run; check the state first.")
            ) from None

    try:
        envelope = response.json()
    except ValueError:
        envelope = None

    if response.is_success and isinstance(envelope, dict) and envelope.get("Success", True):
        return envelope.get("Data")

    status = response.status_code
    detail = None
    if isinstance(envelope, dict):
        detail = envelope.get("Message") or envelope.get("Error")
    route_missing = status == 404 and (not detail or str(detail).startswith(("Route Not Found", "Unsupported")))
    if detail:
        # Nexus already explains itself ("Unknown element type 'X'"); repeating the generic hint only adds noise.
        raise NexusError(f"Nexus {status}: {detail}", status, route_missing)
    raise NexusError(_STATUS_HINTS.get(status, f"Unexpected HTTP status {status}."), status, route_missing)
