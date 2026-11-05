r"""Append-only audit trail of every call that can change something.

One JSON object per line (JSONL) in NEXUS_AUDIT_LOG, by default %LOCALAPPDATA%\NexusMCP\audit.jsonl
(outside the repository). Each entry: UTC timestamp, tool, project, mdb, sha256 and first 200
characters of the script, and the outcome. Passwords and tokens are never passed in here. A failure
to write the log is reported on stderr but never breaks the tool call.
"""

import contextlib
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("nexus-mcp.audit")
HEAD_CHARS = 200


def configure_logging() -> None:
    """Python logging to stderr (stdout belongs to the MCP protocol)."""
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def log_path() -> Path:
    explicit = os.environ.get("NEXUS_AUDIT_LOG")
    if explicit:
        return Path(explicit)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "NexusMCP" / "audit.jsonl"


def record(tool: str, status: str, *, project: str | None = None, mdb: str | None = None,
           script: str | None = None, detail: str | None = None) -> None:
    entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tool": tool,
             "project": project, "mdb": mdb, "status": status}
    if script is not None:
        entry["script_sha256"] = hashlib.sha256(script.encode("utf-8")).hexdigest()
        entry["script_head"] = script[:HEAD_CHARS]
    if detail:
        entry["detail"] = detail[:300]
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as error:
        log.warning("could not write the audit log: %s", error)


@contextlib.contextmanager
def entry(tool: str, *, project: str | None = None, mdb: str | None = None,
          script: str | None = None, detail: str | None = None):
    """Record the outcome of the block: "ok", or "refused"/"error" with the message. Re-raises.

    The block may set `ctx["status"]`, `ctx["project"]`, `ctx["mdb"]` or `ctx["detail"]`.
    """
    ctx = {"status": "ok", "project": project, "mdb": mdb, "detail": detail}
    try:
        yield ctx
    except BaseException as error:
        record(tool, "error", project=ctx["project"], mdb=ctx["mdb"], script=script,
               detail=f"{type(error).__name__}: {error}")
        raise
    record(tool, ctx["status"], project=ctx["project"], mdb=ctx["mdb"], script=script, detail=ctx["detail"])
