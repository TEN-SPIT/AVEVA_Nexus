<#
  install-runtime.ps1 - creates or updates a Nexus runtime folder from launcher\ in the repo.

  Usage:  .\scripts\install-runtime.ps1 -Runtime <dir> [-Force]

  - copies the launcher scripts (*.ps1, *.bat, *.pmlmac) into <dir>; an existing file that differs is kept
    (and reported) unless -Force is given
  - always refreshes config\*.example.json; creates config\projects.json from the example ONLY if missing
    (an existing projects.json / runtime.json is never touched)
  - creates versions\ and run\ ; never touches bin\, current.txt, versions\* or run\*
  Idempotent: running it twice changes nothing.
#>
param(
    [Parameter(Mandatory = $true)][string]$Runtime,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
$src = Join-Path (Split-Path -Parent $PSScriptRoot) 'launcher'
if (-not (Test-Path $src)) { throw "launcher folder not found: $src" }

foreach ($d in $Runtime, (Join-Path $Runtime 'config'), (Join-Path $Runtime 'versions'), (Join-Path $Runtime 'run')) {
    New-Item -ItemType Directory -Force $d | Out-Null
}
$Runtime = (Resolve-Path $Runtime).Path

function Copy-Template([string]$From, [string]$To, [switch]$Overwrite) {
    if ((Test-Path $To) -and -not $Overwrite) {
        if ((Get-FileHash $From).Hash -ne (Get-FileHash $To).Hash) { "  kept (differs, use -Force): $(Split-Path -Leaf $To)" }
        return
    }
    if ((Test-Path $To) -and (Get-FileHash $From).Hash -eq (Get-FileHash $To).Hash) { return }
    Copy-Item $From $To -Force
    "  copied: $(Split-Path -Leaf $To)"
}

"Installing launcher into $Runtime"
Get-ChildItem $src -File | Where-Object { $_.Extension -in '.ps1', '.bat', '.pmlmac' } | ForEach-Object {
    Copy-Template $_.FullName (Join-Path $Runtime $_.Name) -Overwrite:$Force
}
Get-ChildItem (Join-Path $src 'config') -File -Filter *.example.json | ForEach-Object {
    Copy-Template $_.FullName (Join-Path $Runtime "config\$($_.Name)") -Overwrite
}
$projects = Join-Path $Runtime 'config\projects.json'
if (-not (Test-Path $projects)) {
    Copy-Item (Join-Path $src 'config\projects.example.json') $projects
    '  created config\projects.json from the example: edit it'
} else {
    '  config\projects.json exists: left untouched'
}
"Done. Current DLL: " + $(if (Test-Path (Join-Path $Runtime 'current.txt')) { (Get-Content (Join-Path $Runtime 'current.txt') -TotalCount 1) } else { '(no current.txt: bin\ is used)' })
