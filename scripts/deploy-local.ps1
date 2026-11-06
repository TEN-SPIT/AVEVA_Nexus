<#
  deploy-local.ps1 - builds the addin and installs it as a NEW version folder in the runtime:
      <Runtime>\versions\<version>\{AVEVA_Nexux_Addin.dll, Newtonsoft.Json.dll}
  then points <Runtime>\current.txt at it (the previous value is kept in current.txt.prev).
  A running E3D keeps using the folder it loaded, so nothing is locked; it picks the new one at its next start.

  Usage:
    .\scripts\deploy-local.ps1 -Runtime <dir>                      build + install + switch current
    .\scripts\deploy-local.ps1 -Runtime <dir> -Restart -Project X  ... and restart the TTY session for project X
    .\scripts\deploy-local.ps1 -Runtime <dir> -Rollback [-Restart -Project X]
  Test/other options: -DllPath <dll or folder> skips the build (-Version <name> if the DLL has no product version);
  -Force replaces an existing version folder.
#>
param(
    [Parameter(Mandatory = $true)][Alias('Target')][string]$Runtime,
    [string]$Project,            # project code in the runtime's projects.json; needed for -Restart
    [int]$Port = 5001,
    [switch]$Writable,           # with -Restart: start the session with NEXUS_READONLY=0
    [switch]$Restart,            # stop/start the TTY session with the runtime's Stop-Nexus.ps1 / Start-Nexus.ps1
    [switch]$Rollback,           # swap current.txt and current.txt.prev
    [switch]$Force,              # overwrite an existing version folder
    [string]$DllPath,            # use this DLL (file or folder containing it) instead of building
    [string]$Version,            # version name; default: the one the build reports
    [string]$E3dDir = 'C:\Program Files (x86)\AVEVA\Everything3D3.1'
)
$ErrorActionPreference = 'Stop'
# Old usage was "-Target <dir> -Project X [-Writable]" = rebuild AND restart: keep that meaning.
if (($Project -or $Writable) -and -not $Restart) { Write-Host '-Project/-Writable given: restarting the TTY session too (-Restart)'; $Restart = $true }
$stoppedEarly = $false
$Runtime = (Resolve-Path $Runtime).Path
$cur = Join-Path $Runtime 'current.txt'
$prev = Join-Path $Runtime 'current.txt.prev'

function Read-First([string]$Path) {
    if (-not (Test-Path $Path)) { return '' }
    $l = Get-Content $Path -TotalCount 1
    if ($l) { return $l.Trim() } else { return '' }
}
function Write-Line([string]$Path, [string]$Text) { Set-Content -Path $Path -Value $Text -Encoding ASCII }

if ($Rollback) {
    $old = Read-First $prev
    if (-not $old) { throw "Nothing to roll back to: $prev is missing or empty" }
    if (-not (Test-Path (Join-Path $Runtime "versions\$old\AVEVA_Nexux_Addin.dll"))) { throw "Version folder versions\$old no longer exists" }
    $now = Read-First $cur
    Write-Line $cur $old
    if ($now) { Write-Line $prev $now } else { Remove-Item $prev -Force }
    "Rolled back: current = $old (was '$now')"
} else {
    $tmp = $null
    if ($DllPath) {
        $dllFile = if (Test-Path $DllPath -PathType Container) { Join-Path $DllPath 'AVEVA_Nexux_Addin.dll' } else { $DllPath }
        if (-not (Test-Path $dllFile)) { throw "DLL not found: $dllFile" }
        $srcDir = Split-Path -Parent (Resolve-Path $dllFile).Path
        if (-not $Version) { $Version = (Get-Item $dllFile).VersionInfo.ProductVersion }
        if (-not $Version) { throw 'The DLL has no product version: pass -Version' }
    } else {
        $tmp = Join-Path ([IO.Path]::GetTempPath()) ("nexus-build-" + [guid]::NewGuid().ToString('N'))
        $lines = & (Join-Path $PSScriptRoot 'build.ps1') -E3dDir $E3dDir -Out $tmp
        if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
        $lines | ForEach-Object { Write-Host $_ }
        $srcDir = $tmp
        if (-not $Version) {
            $m = $lines | ForEach-Object { "$_" } | Select-String -Pattern '^Built .* \((.+)\)\s*$' | Select-Object -Last 1
            if ($m) { $Version = $m.Matches[0].Groups[1].Value }
            elseif (Test-Path (Join-Path $tmp 'BuildInfo.cs')) {
                $m = Select-String -Path (Join-Path $tmp 'BuildInfo.cs') -Pattern 'AssemblyInformationalVersion\("([^"]+)"\)'
                if ($m) { $Version = $m.Matches[0].Groups[1].Value }
            }
        }
        if (-not $Version) { throw 'Could not read the version from the build output' }
    }
    $name = $Version -replace '[^A-Za-z0-9._+-]', '_'
    # A "-dirty" build gets the same name after every edit on one commit: make each one unique.
    if ($name -match '-dirty$' -and -not $DllPath) { $name += '.' + (Get-Date -Format 'yyyyMMddHHmmss') }
    $dest = Join-Path $Runtime "versions\$name"
    if ($Restart -and $Project) {
        # Stop first: the running session may hold the DLL of the folder -Force is about to replace.
        & (Join-Path $Runtime 'Stop-Nexus.ps1') -Project $Project
        $stoppedEarly = $true
    }
    if (Test-Path $dest) {
        if (-not $Force) { throw "Version folder already exists: $dest (use -Force to replace it)" }
        Remove-Item $dest -Recurse -Force
    }
    New-Item -ItemType Directory -Force $dest | Out-Null
    Copy-Item (Join-Path $srcDir 'AVEVA_Nexux_Addin.dll') $dest -Force
    $nj = Join-Path $srcDir 'Newtonsoft.Json.dll'
    if (Test-Path $nj) { Copy-Item $nj $dest -Force } elseif (-not $DllPath) { throw 'Newtonsoft.Json.dll missing from the build output' }
    if ($tmp) { Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue }

    $now = Read-First $cur
    if ($now -and $now -ne $name) { Write-Line $prev $now }
    Write-Line $cur $name
    "Installed $name; current = $name (previous = '$now')"
}

if ($Restart) {
    if (-not $Project) { throw '-Restart needs -Project <code>' }
    if (-not $stoppedEarly) { & (Join-Path $Runtime 'Stop-Nexus.ps1') -Project $Project }
    Start-Sleep -Seconds 3
    $startArgs = @{ Project = $Project; NoWait = $true }
    if ($Writable) { $startArgs['Writable'] = $true }
    & (Join-Path $Runtime 'Start-Nexus.ps1') @startArgs

    $deadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 3
        try {
            $r = Invoke-RestMethod "http://localhost:$Port/api/Health" -TimeoutSec 3
            if ($r.Success) { "UP (http://localhost:$Port/)"; exit 0 }
        } catch { }
    }
    Write-Error "Timeout: Nexus did not answer on port $Port within 120 s"
    exit 1
}
