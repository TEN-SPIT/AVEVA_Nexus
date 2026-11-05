<#
  Start-Nexus.ps1 - launches AVEVA E3D in TTY (headless) mode for a project and
  starts the Nexus HTTP gateway inside that session.

  Usage:  .\Start-Nexus.ps1 -Project <code> [-Port 5001] [-NoWait]
  Config: <runtime>\config\projects.json  (one entry per project)
  DLL:    <runtime>\versions\<first line of current.txt>\  (fallback: <runtime>\bin\)
#>
param(
    [Parameter(Mandatory = $true)][string]$Project,
    [int]$Port = 0,
    [switch]$NoWait,
    [switch]$Visible,  # normal console window, no output redirection (for debugging)
    [switch]$Writable  # start with NEXUS_READONLY=0: allows PML/Execute and PML/Run (read-only is the default)
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $root 'NexusRuntime.ps1')
$cfgFile = Join-Path $root 'config\projects.json'

$all = Get-Content $cfgFile -Raw | ConvertFrom-Json
$cfg = $all.$Project
if (-not $cfg) { throw "Project '$Project' not found in $cfgFile" }
if ($Port -eq 0) { $Port = [int]$cfg.port }

# LOCAL variant: the gateway listens on localhost only, so no token is required.
$token = ''

$runDir = Join-Path $root "run\$Project"
New-Item -ItemType Directory -Force $runDir | Out-Null
$pidFile = Join-Path $runDir 'e3d.pid'

if (Test-Path $pidFile) {
    $old = Get-Process -Id ([int](Get-Content $pidFile)) -ErrorAction SilentlyContinue
    if ($old) { throw "Nexus for '$Project' already running (PID $($old.Id)). Use Stop-Nexus.ps1 first." }
}

# Session macro: enter module, load the addin from the current version folder, start the HTTP listener
$dllDir = Get-NexusDllDir $root
$addin = Join-Path $dllDir 'AVEVA_Nexux_Addin'
$macro = Join-Path $runDir 'session.pmlmac'
@"
$($cfg.module)
import '$addin'
handle any
endhandle
using namespace 'AVEVA_Nexus.Config'
!!nexusHost = object HostConfig()
!!nexusHost.SetPrefix('http://localhost:$Port/')
!!nexusHost.Start()
"@ | Set-Content $macro -Encoding ASCII

# Environment for the E3D process (inherited by Start-Process)
$env:NEXUS_TOKEN = $token
$env:PDMS_CONSOLE_IDENTIFIER = "nexus$Port."

# E3D login. The password is NOT stored in projects.json. Sources, in order:
#  1. Environment variables set by the MCP server after asking the user (never on this script's command line):
#     NEXUS_E3D_USER, NEXUS_E3D_PASSWORD, or NEXUS_E3D_AUTH=windows (Windows/AD authentication: user only, no password).
#  2. A 'password' field in projects.json (old setup, still honoured).
#  3. Otherwise a Get-Credential prompt (manual use from a console).
$e3dUser = if ($env:NEXUS_E3D_USER) { $env:NEXUS_E3D_USER } else { $cfg.user }
$e3dPass = if ($env:NEXUS_E3D_PASSWORD) { $env:NEXUS_E3D_PASSWORD } else { $cfg.password }
$e3dAuth = $env:NEXUS_E3D_AUTH
$env:NEXUS_E3D_PASSWORD = $null  # do not leave it in this process environment
if ($e3dAuth -eq 'windows') {
    $userPass = $e3dUser
} elseif ($e3dPass) {
    $userPass = "$e3dUser/$e3dPass"
} else {
    $cred = Get-Credential -UserName $e3dUser -Message "E3D login for project $Project"
    if (-not $cred) { throw 'Login cancelled' }
    $userPass = "$($cred.UserName.Split('\')[-1])/$($cred.GetNetworkCredential().Password)"
}
$args = @($cfg.product, $cfg.module3d, 'init', "`"$($cfg.init)`"", 'TTY', $cfg.project, $userPass, $cfg.mdb, "`$M/$macro")
$args = $args | Where-Object { $_ }

# NOTE: launched through WMI (Win32_Process.Create) so E3D is detached from the caller's process tree.
# Started with Start-Process from inside Claude Code, des.exe failed with
# "Unable to open communications file to read"; launched via WMI it works.
$log = Join-Path $runDir 'stdout.log'
# The WMI-created process does not inherit this script's environment, so NEXUS_READONLY is set in the command line.
$writableEnv = if ($Writable) { 'set NEXUS_READONLY=0&& ' } else { '' }
$cmd = "cmd /c cd /d `"$root`" && set PDMS_CONSOLE_IDENTIFIER=nexus$Port.&& $writableEnv`"$($cfg.mon)`" $($args -join ' ') > `"$log`" 2>&1"
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd; CurrentDirectory = $root }
if ($r.ReturnValue -ne 0) { throw "WMI Create failed (code $($r.ReturnValue))" }
$r.ProcessId | Set-Content $pidFile
"Launched E3D TTY for $Project (launcher PID $($r.ProcessId)), port $Port, DLL folder $(Split-Path -Leaf $dllDir)"

if ($NoWait) { return }

$deadline = (Get-Date).AddSeconds(600)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    try {
        $r = Invoke-RestMethod "http://localhost:$Port/api/Health" -TimeoutSec 3
        if ($r.success) { "Nexus is UP: http://$($env:COMPUTERNAME):$Port/"; return }
    } catch { }
}
throw "Timeout waiting for Nexus to answer on port $Port. See $runDir\stdout.log"
