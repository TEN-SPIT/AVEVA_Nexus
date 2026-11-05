<#
  set-e3d-login.ps1 - store the E3D password once in the Windows Credential Manager, so the Nexus MCP
  can start E3D (start_e3d) without any dialog, also when nobody is at the desk.

  Usage:   .\scripts\set-e3d-login.ps1 -Project DEMO [-User SYSTEM]
           .\scripts\set-e3d-login.ps1 -Project DEMO -Forget

  The password is DPAPI-encrypted and bound to your Windows user; only processes running as you can read
  it. It never goes into projects.json, a command line or the chat. To see or remove it by hand:
  Control Panel > Credential Manager > Windows Credentials > "NexusMCP/<PROJECT>/<USER>".
#>
param(
    [Parameter(Mandatory = $true)][string]$Project,  # E3D project code (DEMO), not the projects.json entry name
    [string]$User = 'SYSTEM',
    [switch]$Forget
)
$ErrorActionPreference = 'Stop'
$mcp = Join-Path (Split-Path -Parent $PSScriptRoot) 'mcp'
Push-Location $mcp
try {
    if ($Forget) { uv run python credstore.py delete $Project $User; return }
    # Same topmost login dialog as start_e3d (a console prompt failed when the window lost focus or closed).
    # The password goes from the dialog to the Credential Manager inside one Python process.
    uv run python -c "import sys, e3d_launcher as l, credstore as c; u, p, _ = l._ask_credentials(sys.argv[1], sys.argv[2]); c.write(sys.argv[1], u, p); print('stored ' + c.target(sys.argv[1], u))" $Project $User
} finally { Pop-Location }
