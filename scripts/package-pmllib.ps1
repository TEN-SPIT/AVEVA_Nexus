<#
  package-pmllib.ps1 - assembles the NexusMCP folder that goes into the project PMLLIB:
  the PML functions and the .uic from pmllib\NexusMCP, plus the Nexus DLLs.

  Usage:  .\scripts\package-pmllib.ps1 [-DllDir <folder with AVEVA_Nexux_Addin.dll>] [-Out <folder>]

  The DLLs are not kept in git. By default they are taken from the local working folder where Nexus
  is deployed (..\AVEVA_Nexus_local\bin); pass -DllDir to use another build output.
#>
param(
    [string]$DllDir = (Join-Path (Split-Path -Parent $PSScriptRoot | Split-Path -Parent) 'AVEVA_Nexus_local\bin'),
    [string]$Out = (Join-Path (Split-Path -Parent $PSScriptRoot | Split-Path -Parent) 'AVEVA_Nexus_local\package')
)

$ErrorActionPreference = 'Stop'
$src = Join-Path (Split-Path -Parent $PSScriptRoot) 'pmllib\NexusMCP'
$dest = Join-Path $Out 'NexusMCP'

foreach ($name in 'AVEVA_Nexux_Addin.dll', 'Newtonsoft.Json.dll') {
    if (-not (Test-Path (Join-Path $DllDir $name))) { throw "Missing $name in $DllDir" }
}

if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
New-Item -ItemType Directory -Force (Join-Path $dest 'dll') | Out-Null
Copy-Item (Join-Path $src '*') $dest -Recurse -Force
Copy-Item (Join-Path $DllDir 'AVEVA_Nexux_Addin.dll'), (Join-Path $DllDir 'Newtonsoft.Json.dll') (Join-Path $dest 'dll')

# PML files must be CRLF
Get-ChildItem $dest -Recurse -Include *.pmlfnc | ForEach-Object {
    $text = [IO.File]::ReadAllText($_.FullName) -replace "`r?`n", "`r`n"
    [IO.File]::WriteAllText($_.FullName, $text, (New-Object Text.UTF8Encoding($false)))
}

"Package ready: $dest"
Get-ChildItem $dest -Recurse -File | ForEach-Object { '  ' + $_.FullName.Substring($dest.Length + 1) }
