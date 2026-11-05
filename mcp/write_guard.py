"""Write guard: keeps PML run through the MCP from saving anything outside the databases it may write.

The hard rule is how things get SAVED, not what the model types:
- `SAVEWORK` is refused inside `run_pml` scripts; the only way to commit is `save_work(db)`, which
  refuses any database not allowed for the current MDB and runs `SAVEWORK <db>` (verified on E3D 3.1:
  it saves only that database; pending changes in other databases stay unsaved and disappear when
  the session stops).
- Which databases are allowed is configured per MDB in projects.json (`write_dbs`, glob patterns on
  the database name such as `*/DESI/DEMO-MCP-TEST`). No list means no writing at all.

Two best-effort checks sit on top, to catch mistakes early (they cannot see through variables or
computed names, which is why the commit gate above is the real barrier):
- before a script runs, every explicit reference in it (`=123/45`, `/NAME`) is resolved, and the
  script is refused if one belongs to a database that is not allowed;
- after it runs, if the current element was left in a database that is not allowed it is moved
  back to WORLD, so the next call cannot edit it by accident.
"""

import fnmatch
import re

from mcp.server.mcpserver.exceptions import ToolError

import audit
import e3d_launcher
import nexus_client
import pml_dev

# dbrefs (=123/45) and element names (/NAME). A name must not be glued to a word, so 5/6 is not a name.
_REFERENCE = re.compile(r"=\d+/\d+|(?<![\w)\]])/[A-Za-z0-9_][\w.\-]*")
_DB_NAME = re.compile(r"[A-Za-z0-9_\-./]+")
_DBNAME_LINE = re.compile(r"^Dbname\s+(\S+)", re.IGNORECASE)
_MAX_REFERENCES = 60


def is_allowed(db: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(db, pattern) for pattern in patterns)


def session_info() -> dict:
    return nexus_client.call("GET", "/api/Session/Info")


def write_patterns(info: dict) -> list[str]:
    """Allowed database patterns for the MDB of the running session (empty if none are configured)."""
    for cfg in e3d_launcher._load_projects().values():
        if cfg.get("project") == info.get("Project") and cfg.get("mdb", "").lstrip("/") == info.get("Mdb"):
            return list(cfg.get("write_dbs", []))
    return []


def has_save_command(script: str) -> bool:
    return pml_dev.has_save_command(script)


def references(script: str) -> list[str]:
    seen: dict[str, None] = {}
    for match in _REFERENCE.finditer(script):
        seen.setdefault(match.group(0), None)
    return list(seen)


def _databases_of(tokens: list[str]) -> dict[str, str]:
    """Database of each element reference that exists; unknown or not-yet-created names are skipped."""
    if not tokens:
        return {}
    lines = []
    for index, token in enumerate(tokens):
        lines += [f"$P NXMARK{index}", f"Q DBNAME OF {token}"]
    log = pml_dev.run_with_log("\n".join(lines), None)["Log"]
    found, current, seen = {}, None, set()
    for line in log.splitlines():
        # Markers carry no '@': in graphical E3D `$P @@0` prints just "@" (seen live, 2026-11-05).
        marker = re.match(r"^NXMARK(\d+)$", line.strip())
        if marker:
            current = int(marker.group(1))
            seen.add(current)
            continue
        match = _DBNAME_LINE.match(line.strip())
        if match and current is not None:
            found[tokens[current]] = match.group(1)
    if seen != set(range(len(tokens))):
        # Fail closed: without the log we cannot tell which database each reference is in.
        raise ToolError(
            "Cannot verify which databases the script touches (the E3D log was not captured: Nexus on "
            "another machine, a temp path with spaces, or an empty log). Refusing to run it unchecked."
        )
    return found


def _require_writable(info: dict) -> list[str]:
    if info.get("ReadOnly", True):
        raise ToolError("Nexus is in read-only mode: nothing can be written (start_e3d with writable=true).")
    patterns = write_patterns(info)
    if not patterns:
        raise ToolError(
            f"No writable databases are configured for MDB '{info.get('Mdb')}'. Add 'write_dbs' "
            "(glob patterns on database names) to that project's entry in projects.json."
        )
    return patterns


def guarded_run(script: str, variables: list[str] | None) -> dict:
    """run_pml with the write guard around it. Writable runs are recorded in the audit log."""
    info = session_info()
    if info.get("ReadOnly", True):
        return pml_dev.run_with_log(script, variables)  # Nexus itself refuses; keep its message

    with audit.entry("run_pml", project=info.get("Project"), mdb=info.get("Mdb"), script=script) as record:
        result = _guarded_writable_run(info, script, variables)
        if not result.get("Ok", True):
            record["status"] = "pml_errors"
        return result


def _guarded_writable_run(info: dict, script: str, variables: list[str] | None) -> dict:
    patterns = _require_writable(info)
    reason = pml_dev.forbidden_in_writable(script)
    if reason:
        raise ToolError(reason)

    tokens = references(script)
    if len(tokens) > _MAX_REFERENCES:
        raise ToolError(
            f"The script refers to {len(tokens)} distinct elements (max {_MAX_REFERENCES} can be checked). "
            "Split it into several smaller run_pml calls."
        )
    offenders = {t: db for t, db in _databases_of(tokens).items() if not is_allowed(db, patterns)}
    if offenders:
        listing = ", ".join(f"{token} (database {db})" for token, db in offenders.items())
        raise ToolError(f"Refused: the script refers to elements outside the writable databases: {listing}. Allowed: {', '.join(patterns)}.")

    result = pml_dev.run_with_log(script, variables)

    # Leave the session parked on something safe.
    where = pml_dev.run_with_log("$P NXMARKce\nQ DBNAME", None)["Log"]
    current_db = next((m.group(1) for line in where.splitlines() if (m := _DBNAME_LINE.match(line.strip()))), None)
    if current_db is None:
        pml_dev.run_with_log("CE /*", None)
        result["Warnings"] = ["Could not read the database of the current element; moved it back to WORLD to be safe."]
    elif not is_allowed(current_db, patterns):
        pml_dev.run_with_log("CE /*", None)
        result["Warnings"] = [f"The current element was left in database {current_db}, which is not writable; moved it back to WORLD."]
    return result


def claims_pending(port: int | None = None) -> list[str] | None:
    """Elements the session still holds claimed (on the Nexus of `port`, default the active one).

    [] if there are none, or if it cannot be asked because Nexus is not reachable or read-only
    (nothing can be claimed there). None means UNKNOWN: Nexus answered but the log did not carry
    the answer, so the caller must not assume "no claims".
    """
    url = nexus_client.url_for_port(port) if port else None
    if port and url is None:
        return None   # remote Nexus: the port of a local project says nothing about which session this is
    try:
        with nexus_client.override_url(url):
            log = pml_dev.run_with_log("$P NXMARKclaims\nQ CLAIMLIST", None)["Log"]
    except nexus_client.NexusError as error:
        # Only "nothing is running" or "read-only" (nothing can be claimed) mean no claims; a timeout,
        # a 500 or a lost connection mean we do not know.
        return [] if error.unreachable or error.status == 403 else None
    except ToolError:
        return None
    lines = [line.strip() for line in log.splitlines() if line.strip()]
    if "NXMARKclaims" not in lines:
        return None
    after = lines[lines.index("NXMARKclaims") + 1:]
    return [] if not after or after[0].lower() == "none" else after


def save_work(db: str) -> dict:
    """Commit the pending changes of ONE allowed database."""
    info = session_info()
    patterns = _require_writable(info)
    if not _DB_NAME.fullmatch(db):
        raise ToolError("Invalid database name.")
    if not is_allowed(db, patterns):
        raise ToolError(f"Database '{db}' is not writable through the MCP. Allowed: {', '.join(patterns)}.")
    with audit.entry("save_work", project=info.get("Project"), mdb=info.get("Mdb"), script=f"SAVEWORK {db}"):
        return _save_work(db)


def _save_work(db: str) -> dict:
    result = pml_dev.run_with_log(f"SAVEWORK {db}", None)
    if not result["Ok"]:
        raise ToolError(f"SAVEWORK {db} failed: {result['Log'] or result['Errors']}")

    # In a MULTIWRITE database everything this session creates or edits stays claimed by it, and
    # SAVEWORK does not release the claims: other sessions could not edit those elements. Release
    # them right after saving. (It only gives up claims; it does not change any data.)
    released = pml_dev.run_with_log("UNCLAIM ALL", None)
    outcome = {"saved": db, "log": result["Log"], "claims_released": released["Ok"]}
    if not released["Ok"]:
        outcome["warning"] = (
            "SAVEWORK worked but UNCLAIM ALL failed, so elements may still be claimed by this session: "
            f"{released['Log'] or released['Errors']}"
        )
    return outcome


def run_query_pml(script: str, variables: list[str] | None = None) -> dict:
    """Run read-only helper PML (`Q ...` lookups, scratch variables) that the MCP itself builds.

    Skips the database checks of guarded_run (a lookup of an element in a locked database is fine)
    but still refuses SAVEWORK, so no tool can use it to commit.
    """
    if has_save_command(script):
        raise ToolError("SAVEWORK is not allowed here. Use the save_work tool.")
    return pml_dev.run_with_log(script, variables)
