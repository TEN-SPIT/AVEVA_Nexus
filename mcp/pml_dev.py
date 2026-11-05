"""PML development helpers: run PML and get the REAL error text back, and keep test code in a sandbox.

Why this exists: in a headless (TTY) E3D session, PML error messages go nowhere: Nexus only reports
"PML command failed: <line>". The E3D command `ALPHA LOG /<path>` writes everything the command
window would show (output of `$P`, error text, error code and the position of the failure) to a
file, so we wrap every script with it and hand the file back as `Log`.

Sandbox: functions and objects are loaded by E3D from the folders listed in its PMLLIB. The
session's PMLLIB already contains C:\\Temp\\PmlRepos, so a file saved under it is found by name and
reloaded automatically when edited. To keep that from hurting the user's real tools, everything
written by this module:
- lives in ONE fixed sandbox folder (NEXUS_PML_SANDBOX),
- must be called `mcp...` (and define `mcp...`), so it can never shadow a real function or object.
Saving a file does not execute anything; running PML stays blocked in read-only sessions.
"""

import os
import re
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

from mcp.server.mcpserver.exceptions import ToolError

import nexus_client

SANDBOX = Path(os.environ.get("NEXUS_PML_SANDBOX", r"C:\Temp\PMLRepos\_mcp_sandbox"))
LOG_DIR = Path(tempfile.gettempdir()) / "nexus-mcp-logs"
MAX_LOG_CHARS = 20000
MAX_FILE_BYTES = 100_000

# kind -> (subfolder, extension, regex that captures the name being defined)
_KINDS = {
    "function": ("fnc", ".pmlfnc", re.compile(r"define\s+function\s+!!(\w+)", re.IGNORECASE)),
    "object": ("obj", ".pmlobj", re.compile(r"define\s+object\s+(\w+)", re.IGNORECASE)),
    "form": ("frm", ".pmlfrm", re.compile(r"setup\s+form\s+!!(\w+)", re.IGNORECASE)),
}
_NAME = re.compile(r"^mcp[a-z0-9_]{0,40}$")
# SAVEWORK can be abbreviated (SAV, SAVE, SAVEW...); any statement starting with "sav" counts.
SAVE_COMMAND = re.compile(r"(?im)(?:^|;)\s*sav\w*")


def _is_local() -> bool:
    return urlparse(nexus_client.NEXUS_URL).hostname in ("localhost", "127.0.0.1", "::1")


def _read_log(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace").replace("\r", "").strip()
    except FileNotFoundError:
        return ""
    finally:
        path.unlink(missing_ok=True)
    if len(text) > MAX_LOG_CHARS:
        text = text[:MAX_LOG_CHARS] + f"\n... (log cut at {MAX_LOG_CHARS} characters)"
    return text


def run_with_log(script: str, variables: list[str] | None) -> dict:
    """Run a PML script through Nexus and add `Log`, the text E3D printed (output and real errors)."""
    log_path = None
    if _is_local():
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / f"{uuid.uuid4().hex}.log"
        if " " not in str(log_path):  # PML paths are not quoted here, so spaces would break the command
            script = f"ALPHA LOG /{str(log_path).replace(chr(92), '/')}\n{script}\nALPHA LOG END"
        else:
            log_path = None

    body = {"script": script}
    if variables is not None:
        body["return"] = variables
    data = nexus_client.call("POST", "/api/PML/Run", body=body)

    errors = [e for e in data.get("Errors", []) if not e.startswith("PML command failed: ALPHA LOG")]
    result = {"Ok": not errors, "Errors": errors, "Values": data.get("Values", {})}
    if log_path is not None:
        result["Log"] = _read_log(log_path)
    else:
        result["Log"] = "(not captured: Nexus is not on this machine, or the temp path has spaces)"
    return result


def _check(kind: str, name: str) -> tuple[Path, re.Pattern]:
    if kind not in _KINDS:
        raise ToolError(f"Unknown kind '{kind}'. Use one of: {', '.join(_KINDS)}.")
    name = name.lower()
    if not _NAME.match(name):
        raise ToolError(
            "Names must be lowercase, start with 'mcp' and use only letters, digits and '_' "
            "(for example 'mcpecho'). The prefix guarantees test code never replaces a real tool."
        )
    folder, extension, pattern = _KINDS[kind]
    return SANDBOX / folder / f"{name}{extension}", pattern


def save(kind: str, name: str, content: str) -> dict:
    """Write one PML source file into the sandbox. Returns where it went."""
    path, pattern = _check(kind, name)
    name = name.lower()

    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise ToolError(f"File too large (max {MAX_FILE_BYTES} bytes).")
    if SAVE_COMMAND.search(content):
        raise ToolError(
            "Test code may not contain SAVEWORK: saving goes only through the save_work tool, "
            "which checks the database. A saved function could otherwise bypass that."
        )
    defined = {n.lower() for n in pattern.findall(content)}
    if defined != {name}:
        raise ToolError(
            f"The file must define exactly one {kind} named '{name}' (E3D finds it by file name); "
            f"found: {sorted(defined) or 'none'}."
        )
    try:
        data = content.replace("\r\n", "\n").replace("\n", "\r\n").encode("cp1252")  # PML files are CRLF/ANSI
    except UnicodeEncodeError:
        raise ToolError("The code contains characters that cannot be saved as ANSI (cp1252).") from None

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": str(path), "bytes": len(data)}


def delete(kind: str, name: str) -> dict:
    path, _ = _check(kind, name)
    existed = path.exists()
    path.unlink(missing_ok=True)
    return {"deleted": existed, "path": str(path)}


def listing() -> dict:
    if not SANDBOX.exists():
        return {"sandbox": str(SANDBOX), "files": []}
    files = sorted(str(p.relative_to(SANDBOX)) for p in SANDBOX.rglob("*") if p.is_file())
    return {"sandbox": str(SANDBOX), "files": files}
