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

# ---- static scan of PML text ---------------------------------------------------------------
# BEST EFFORT ONLY. PML can build commands at run time (`$!x` expansion, macros, computed names),
# so no static scan can prove a script harmless. The real barrier is that saving goes only through
# save_work (which checks the database) and that Nexus itself is read-only unless the user allowed
# writing. This scan catches the obvious ways around that, and mistakes, early.
#
# Text is first cleaned: string literals (|..|, '..', "..") are blanked and comments (`--`, `$*`)
# are cut, so words inside them are never flagged. Then a word counts as a COMMAND only where a
# statement can start: line start, after `;`, after then/else/do/any (`handle any`), after `)`
# (`handle (n,m)`), or after a `$x` command prefix other than `$P` (which just prints text).
# Words after `/` (names), `:` (attributes), `!` or `.` are therefore never flagged.
_STATEMENT_START = r"(?:^|;|\b(?i:then|else|do|any)\b|\)|\$[A-OQ-Za-oq-z]\s+|\$\s+)\s*"
# SAVEWORK is refused wherever it stands as a bare word, not only at a statement start: PML has more
# places where a command can follow on the same line (one-line do loops...) than this scan models.
# Bare means not glued to a name (/), attribute (:), variable (!), method (.), $ prefix or a hyphenated word.
_SAVE_CMD = re.compile(r"(?<![\w/:!.$@\-])(?i:sav\w*)\b")
_SYNONYM_CMD = re.compile(_STATEMENT_START + r"\$[sS]\b")
_MACRO_CMD = re.compile(_STATEMENT_START + r"\$[mM]\b")
_EXPAND_CMD = re.compile(_STATEMENT_START + r"\$!")
_ALPHA_LOG = re.compile(_STATEMENT_START + r"(?i:alp\w*\s+log)\b")
_FILE_IO = re.compile(
    r"(?i)\bobject\s+(?:file|[\w.]*\bio\b)|\.\s*(?:writefile|readfile|open|writerecord|readrecord|"
    r"writeline|readline|copyfile|deletefile|appendfile)\s*\("
)
_PRINT_TEXT = re.compile(r"(?i)^((?:[^;]*;)*?\s*)\$p\b[^;]*")


def _clean(text: str) -> list[str]:
    """Lines of `text` with strings blanked and comments removed, text of $P lines dropped."""
    out = []
    for line in text.replace("\r", "").split("\n"):
        res, i = [], 0
        while i < len(line):
            ch = line[i]
            if ch in "|'\"":
                end = line.find(ch, i + 1)
                if end != -1:
                    res.append('""')
                    i = end + 1
                    continue
            elif line.startswith("--", i) or line.startswith("$*", i):
                break
            res.append(ch)
            i += 1
        # `$P free text` only prints: its words are text, not commands (what follows a `;` still counts).
        out.append(_PRINT_TEXT.sub(r"\1$P", "".join(res)))
    return out


def has_save_command(text: str) -> bool:
    """True if the text runs SAVEWORK (or an abbreviation such as SAV/SAVEW) as a command."""
    return any(_SAVE_CMD.search(line) for line in _clean(text))


def forbidden_in_writable(text: str) -> str | None:
    """Reason why `text` may not run in a writable session, or None. Best effort (see above)."""
    for line in _clean(text):
        if _SAVE_CMD.search(line):
            return "SAVEWORK is not allowed inside run_pml. Use the save_work tool, which only saves allowed databases."
        if _MACRO_CMD.search(line):
            return "Running macros ($M) is not allowed: a macro could contain commands this server cannot check."
        if _SYNONYM_CMD.search(line):
            return "Defining synonyms ($S) is not allowed: a synonym can hide a command this server cannot check."
        if _EXPAND_CMD.search(line):
            return "Running a command built from a variable ($!x / $!!x as a statement) is not allowed: it cannot be checked."
        if _FILE_IO.search(line):
            return "File input/output (object FILE, .open/.writefile ...) is not allowed in writable runs."
        if _ALPHA_LOG.search(line):
            return "ALPHA LOG is not allowed: the server owns the log file."
    return None


def _is_local() -> bool:
    return urlparse(nexus_client.base_url()).hostname in ("localhost", "127.0.0.1", "::1")


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
    if not _NAME.fullmatch(name):
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
    if has_save_command(content):
        raise ToolError(
            "Test code may not contain SAVEWORK: saving goes only through the save_work tool, "
            "which checks the database. A saved function could otherwise bypass that."
        )
    reason = forbidden_in_writable(content)
    if reason and "SAVEWORK" not in reason:
        raise ToolError(f"Test code refused: {reason}")
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
