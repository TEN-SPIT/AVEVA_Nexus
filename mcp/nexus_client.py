"""Thin HTTP client for the Nexus add-in running inside E3D.

Every Nexus response is an envelope: {"Success", "Data", "Error", "Message", "RequestId"}.
This module sends the request, opens the envelope and returns only `Data`.
Any failure becomes a NexusError with a short message the model can act on.
"""

import os
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

NEXUS_URL = os.environ.get("NEXUS_URL", "http://localhost:5001")
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
    """


def call(method: str, path: str, *, params: dict | None = None, body: dict | None = None) -> Any:
    """Call a Nexus route and return the envelope's `Data`."""
    headers = {"X-Nexus-Token": NEXUS_TOKEN} if NEXUS_TOKEN else {}
    try:
        response = httpx.request(
            method,
            f"{NEXUS_URL}{path}",
            params=params,
            json=body,
            headers=headers,
            timeout=60,
        )
    except httpx.ConnectError:
        raise NexusError(
            f"Cannot reach Nexus at {NEXUS_URL}. Is E3D running with Nexus started?"
        ) from None
    except httpx.TimeoutException:
        raise NexusError("Nexus did not answer in time (E3D may be busy with another request).") from None

    try:
        envelope = response.json()
    except ValueError:
        envelope = None

    if response.is_success and isinstance(envelope, dict) and envelope.get("Success", True):
        return envelope.get("Data")

    detail = None
    if isinstance(envelope, dict):
        detail = envelope.get("Message") or envelope.get("Error")
    if detail:
        # Nexus already explains itself ("Unknown element type 'X'"); repeating the generic hint only adds noise.
        raise NexusError(f"Nexus {response.status_code}: {detail}")
    raise NexusError(_STATUS_HINTS.get(response.status_code, f"Unexpected HTTP status {response.status_code}."))
