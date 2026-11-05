"""Start and stop the local E3D + Nexus session by wrapping Start-Nexus.ps1 / Stop-Nexus.ps1.

Those scripts live in the local working folder (NEXUS_LOCAL_DIR; default: a folder
AVEVA_Nexus_local next to the repository clone)
and already handle the hard parts: launching E3D detached through WMI, read-only vs writable,
and cleaning orphan processes. This module only validates the request and waits for Nexus.

Security notes:
- config/projects.json holds E3D credentials. Only the non-sensitive fields are ever read
  out of it (see `_PUBLIC_FIELDS`); user and password never reach the model.
- Writable sessions allow PML that changes the model. They are refused unless the project
  is listed in the NEXUS_WRITABLE_PROJECTS environment variable (comma separated, empty
  by default), so that "never write to a production project" does not depend on the model.
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path

import httpx
from mcp.server.mcpserver.exceptions import ToolError

import credstore

# Default: AVEVA_Nexus_local beside the clone (<clone>/mcp/e3d_launcher.py -> <clone>/../AVEVA_Nexus_local).
_DEFAULT_LOCAL_DIR = Path(__file__).resolve().parents[1].parent / "AVEVA_Nexus_local"
LOCAL_DIR = Path(os.environ.get("NEXUS_LOCAL_DIR") or _DEFAULT_LOCAL_DIR)
WRITABLE_PROJECTS = {
    name.strip() for name in os.environ.get("NEXUS_WRITABLE_PROJECTS", "").split(",") if name.strip()
}

_PUBLIC_FIELDS = ("project", "mdb", "module", "port", "auth")
_START_TIMEOUT_SECONDS = 120  # Nexus normally answers about 30 s after launch


def _load_projects() -> dict:
    config = LOCAL_DIR / "config" / "projects.json"
    try:
        return json.loads(config.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise ToolError(f"Local Nexus folder not found: {config} does not exist.") from None


def _login_source(cfg: dict) -> str:
    """How start() will log in: "windows", "stored" (Credential Manager, no dialog) or "dialog"."""
    if cfg.get("auth", "password") == "windows":
        return "windows"
    try:
        stored = credstore.read(cfg.get("project", ""), cfg.get("user") or "")
    except OSError:
        stored = None
    return "stored" if stored else "dialog"


def list_projects() -> dict:
    """Configured projects with their non-sensitive fields only (plus how the login will happen)."""
    return {
        name: {**{field: cfg.get(field) for field in _PUBLIC_FIELDS}, "login": _login_source(cfg)}
        for name, cfg in _load_projects().items()
    }


def _health(port: int) -> bool:
    try:
        return httpx.get(f"http://localhost:{port}/api/Health", timeout=3).is_success
    except httpx.HTTPError:
        return False


# The password ends up on the command line of mon.exe (inside a `cmd /c ...` line), so anything
# cmd or PML would interpret is refused instead of escaped.
_FORBIDDEN_IN_LOGIN = set('&|<>^%"\'`\r\n\t ')


def _check_login(user: str, password: str) -> None:
    if not user or any(ch in _FORBIDDEN_IN_LOGIN for ch in user + password):
        raise ToolError(
            "The user name or password contains a character that cannot be passed safely on "
            "the E3D command line (spaces and & | < > ^ % quotes). E3D was not started."
        )


def _ask_credentials(project: str, default_user: str) -> tuple[str, str, bool]:
    """Show a login dialog on the user's desktop and return (user, password, remember).

    The password goes from the dialog straight to this process; it is never part of a tool
    argument or result, so the model and the conversation never see it. If the user ticks
    "Remember", it is stored in the Windows Credential Manager once E3D has started with it.
    """
    # Own WinForms dialog instead of Get-Credential: that one opened behind other windows (or not at
    # all) when launched from a hidden process. This one is topmost and in the taskbar. Output format:
    # line 1 user, line 2 password, line 3 "remember" or empty; nothing if cancelled.
    command = r"""
Add-Type -AssemblyName System.Windows.Forms
[Windows.Forms.Application]::EnableVisualStyles()
$f = New-Object Windows.Forms.Form
$f.Text = 'E3D login'; $f.TopMost = $true; $f.StartPosition = 'CenterScreen'
$f.ClientSize = New-Object Drawing.Size(360, 196); $f.FormBorderStyle = 'FixedDialog'
$f.MaximizeBox = $false; $f.MinimizeBox = $false; $f.ShowInTaskbar = $true
$m = New-Object Windows.Forms.Label; $m.Text = $env:NEXUS_DLG_MESSAGE; $m.SetBounds(12, 10, 336, 36)
$lu = New-Object Windows.Forms.Label; $lu.Text = 'User'; $lu.SetBounds(12, 52, 80, 20)
$tu = New-Object Windows.Forms.TextBox; $tu.Text = $env:NEXUS_DLG_USER; $tu.SetBounds(100, 50, 248, 22)
$lp = New-Object Windows.Forms.Label; $lp.Text = 'Password'; $lp.SetBounds(12, 84, 80, 20)
$tp = New-Object Windows.Forms.TextBox; $tp.UseSystemPasswordChar = $true; $tp.SetBounds(100, 82, 248, 22)
$rm = New-Object Windows.Forms.CheckBox; $rm.Text = 'Remember on this computer (Windows Credential Manager)'; $rm.Checked = $true; $rm.SetBounds(12, 114, 336, 22)
$ok = New-Object Windows.Forms.Button; $ok.Text = 'OK'; $ok.DialogResult = 'OK'; $ok.SetBounds(192, 152, 75, 28)
$no = New-Object Windows.Forms.Button; $no.Text = 'Cancel'; $no.DialogResult = 'Cancel'; $no.SetBounds(273, 152, 75, 28)
$f.AcceptButton = $ok; $f.CancelButton = $no
$f.Controls.AddRange(@($m, $lu, $tu, $lp, $tp, $rm, $ok, $no))
$f.Add_Shown({ $f.Activate(); $tp.Focus() })
if ($f.ShowDialog() -eq 'OK') { $tu.Text; $tp.Text; if ($rm.Checked) { 'remember' } else { '' } }
"""
    env = {
        **os.environ,
        "NEXUS_DLG_USER": default_user,
        "NEXUS_DLG_MESSAGE": f"E3D login for project {project} (Nexus MCP)",
    }
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env, timeout=180,
        )
    except subprocess.TimeoutExpired:
        raise ToolError("Nobody answered the E3D login dialog within 3 minutes.") from None

    lines = result.stdout.splitlines()
    if result.returncode != 0 or len(lines) < 2 or not lines[1]:
        raise ToolError("E3D login cancelled (or no password entered). E3D was not started.")
    user, password = lines[0].split("\\")[-1].strip(), lines[1]
    _check_login(user, password)
    remember = len(lines) > 2 and lines[2].strip() == "remember"
    return user, password, remember


def _run_script(script: str, *args: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(LOCAL_DIR / script), *args],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=90,
            env={**os.environ, **(extra_env or {})},
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"{script} did not finish in 90 s.") from None


def _tail(text: str, lines: int = 8) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def _known_project(project: str) -> dict:
    # Validated against the config keys: the name is never trusted as-is on a command line.
    projects = _load_projects()
    if project not in projects:
        raise ToolError(f"Unknown project '{project}'. Configured projects: {', '.join(projects)}.")
    return projects[project]


def start(project: str, writable: bool = False) -> dict:
    cfg = _known_project(project)
    port = int(cfg["port"])

    if writable and project not in WRITABLE_PROJECTS:
        raise ToolError(
            f"Writable sessions are not allowed for '{project}'. The user must add it to the "
            "NEXUS_WRITABLE_PROJECTS environment variable of the MCP server first."
        )

    if _health(port):
        return {
            "status": "already_running",
            "port": port,
            "note": "Nexus already answers on this port. Use session_info to see whether it is "
                    "read-only; to change mode, stop_e3d first and start again.",
        }

    # Login, in order: Windows/AD auth (user only); the password stored in the Windows Credential
    # Manager (no dialog, so E3D can start while nobody is at the desk); otherwise a dialog, whose
    # "Remember" box stores the password once E3D has really started with it.
    default_user = cfg.get("user") or ""
    to_remember = None
    source = _login_source(cfg)
    if source == "windows":
        login_env = {"NEXUS_E3D_AUTH": "windows", "NEXUS_E3D_USER": default_user}
    elif source == "stored":
        password = credstore.read(cfg["project"], default_user)
        _check_login(default_user, password)
        login_env = {"NEXUS_E3D_USER": default_user, "NEXUS_E3D_PASSWORD": password}
    else:
        user, password, remember = _ask_credentials(project, default_user)
        login_env = {"NEXUS_E3D_USER": user, "NEXUS_E3D_PASSWORD": password}
        if remember:
            to_remember = (user, password)

    args = ["-Project", project, "-NoWait"] + (["-Writable"] if writable else [])
    result = _run_script("Start-Nexus.ps1", *args, extra_env=login_env)
    if result.returncode != 0:
        raise ToolError(f"Start-Nexus.ps1 failed: {_tail(result.stderr or result.stdout)}")

    log = LOCAL_DIR / "run" / project / "stdout.log"
    launched_at = time.time()
    deadline = time.monotonic() + _START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _health(port):
            if to_remember:
                try:
                    credstore.write(cfg["project"], *to_remember)
                except OSError:
                    pass  # remembering is a convenience; the session is up anyway
            return {"status": "started", "port": port, "read_only": not writable, "login": source}
        failure = _login_failure(log, launched_at)
        if failure:
            hint = (" The stored password may be outdated: run scripts\\set-e3d-login.ps1 again, or "
                    "call forget_e3d_login and start again to get the dialog." if source == "stored" else "")
            raise ToolError(f"E3D refused to start: {failure}.{hint}")
        time.sleep(1)
    return {
        "status": "starting",
        "port": port,
        "note": f"Launched but Nexus did not answer within {_START_TIMEOUT_SECONDS} s. "
                f"Try session_info again shortly; log: {log}",
    }


# What E3D's monitor prints when the login itself is refused. Only used to stop waiting early.
_LOGIN_FAILURE = re.compile(
    r"(?im)^.*(invalid (user|password)|incorrect password|password.{0,30}(incorrect|invalid|wrong)"
    r"|not a valid user|unknown user).*$"
)


def _login_failure(log: Path, since: float = 0.0) -> str | None:
    try:
        if log.stat().st_mtime < since - 2:  # an old log from a previous session
            return None
        text = log.read_text(encoding="utf-8", errors="replace")[-4000:]
    except OSError:
        return None
    match = _LOGIN_FAILURE.search(text)
    return match.group(0).strip()[:200] if match else None


def forget_login(project: str) -> dict:
    """Delete the stored E3D password of a project (the next start shows the dialog again)."""
    cfg = _known_project(project)
    removed = credstore.delete(cfg["project"], cfg.get("user") or "")
    return {"status": "forgotten" if removed else "nothing_stored", "login": _login_source(cfg)}


def stop(project: str) -> dict:
    _known_project(project)
    result = _run_script("Stop-Nexus.ps1", "-Project", project)
    if result.returncode != 0:
        raise ToolError(f"Stop-Nexus.ps1 failed: {_tail(result.stderr or result.stdout)}")
    return {"status": "stopped", "details": result.stdout.strip().splitlines()}
