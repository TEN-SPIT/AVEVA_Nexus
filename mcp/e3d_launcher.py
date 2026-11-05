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
import subprocess
import time
from pathlib import Path

import httpx
from mcp.server.mcpserver.exceptions import ToolError

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


def list_projects() -> dict:
    """Configured projects with their non-sensitive fields only."""
    return {
        name: {field: cfg.get(field) for field in _PUBLIC_FIELDS}
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


def _ask_credentials(project: str, default_user: str) -> tuple[str, str]:
    """Show the native Windows credential dialog on the user's desktop and return (user, password).

    The password goes from the dialog straight to this process; it is never part of a tool
    argument or result, so the model and the conversation never see it. It is not stored.
    """
    # Own WinForms dialog instead of Get-Credential: that one opened behind other windows (or not at
    # all) when launched from a hidden process. This one is topmost and in the taskbar. Output format:
    # line 1 user, line 2 password; nothing if cancelled.
    command = r"""
Add-Type -AssemblyName System.Windows.Forms
[Windows.Forms.Application]::EnableVisualStyles()
$f = New-Object Windows.Forms.Form
$f.Text = 'E3D login'; $f.TopMost = $true; $f.StartPosition = 'CenterScreen'
$f.ClientSize = New-Object Drawing.Size(360, 170); $f.FormBorderStyle = 'FixedDialog'
$f.MaximizeBox = $false; $f.MinimizeBox = $false; $f.ShowInTaskbar = $true
$m = New-Object Windows.Forms.Label; $m.Text = $env:NEXUS_DLG_MESSAGE; $m.SetBounds(12, 10, 336, 36)
$lu = New-Object Windows.Forms.Label; $lu.Text = 'User'; $lu.SetBounds(12, 52, 80, 20)
$tu = New-Object Windows.Forms.TextBox; $tu.Text = $env:NEXUS_DLG_USER; $tu.SetBounds(100, 50, 248, 22)
$lp = New-Object Windows.Forms.Label; $lp.Text = 'Password'; $lp.SetBounds(12, 84, 80, 20)
$tp = New-Object Windows.Forms.TextBox; $tp.UseSystemPasswordChar = $true; $tp.SetBounds(100, 82, 248, 22)
$ok = New-Object Windows.Forms.Button; $ok.Text = 'OK'; $ok.DialogResult = 'OK'; $ok.SetBounds(192, 126, 75, 28)
$no = New-Object Windows.Forms.Button; $no.Text = 'Cancel'; $no.DialogResult = 'Cancel'; $no.SetBounds(273, 126, 75, 28)
$f.AcceptButton = $ok; $f.CancelButton = $no
$f.Controls.AddRange(@($m, $lu, $tu, $lp, $tp, $ok, $no))
$f.Add_Shown({ $f.Activate(); $tp.Focus() })
if ($f.ShowDialog() -eq 'OK') { $tu.Text; $tp.Text }
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
    if not user or any(ch in _FORBIDDEN_IN_LOGIN for ch in user + password):
        raise ToolError(
            "The user name or password contains a character that cannot be passed safely on "
            "the E3D command line (spaces and & | < > ^ % quotes). E3D was not started."
        )
    return user, password


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

    # Login: "password" (default) asks the user in a dialog every time; "windows" relies on
    # Windows/Active Directory authentication (user only, no password; not yet tested on E3D 3.1).
    default_user = cfg.get("user") or ""
    if cfg.get("auth", "password") == "windows":
        login_env = {"NEXUS_E3D_AUTH": "windows", "NEXUS_E3D_USER": default_user}
    else:
        user, password = _ask_credentials(project, default_user)
        login_env = {"NEXUS_E3D_USER": user, "NEXUS_E3D_PASSWORD": password}

    args = ["-Project", project, "-NoWait"] + (["-Writable"] if writable else [])
    result = _run_script("Start-Nexus.ps1", *args, extra_env=login_env)
    if result.returncode != 0:
        raise ToolError(f"Start-Nexus.ps1 failed: {_tail(result.stderr or result.stdout)}")

    deadline = time.monotonic() + _START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _health(port):
            return {"status": "started", "port": port, "read_only": not writable}
        time.sleep(3)
    return {
        "status": "starting",
        "port": port,
        "note": f"Launched but Nexus did not answer within {_START_TIMEOUT_SECONDS} s. "
                f"Try session_info again shortly; log: {LOCAL_DIR / 'run' / project / 'stdout.log'}",
    }


def stop(project: str) -> dict:
    _known_project(project)
    result = _run_script("Stop-Nexus.ps1", "-Project", project)
    if result.returncode != 0:
        raise ToolError(f"Stop-Nexus.ps1 failed: {_tail(result.stderr or result.stdout)}")
    return {"status": "stopped", "details": result.stdout.strip().splitlines()}
