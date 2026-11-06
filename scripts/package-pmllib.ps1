<#
  package-pmllib.ps1 - assembles the NexusMCP folder that goes into the project PMLLIB:
  the PML functions and the .uic from pmllib\NexusMCP, plus the Nexus DLLs.

  Usage:  .\scripts\package-pmllib.ps1 [-Runtime <dir>] [-DllDir <folder with AVEVA_Nexux_Addin.dll>] [-Out <folder>]

  The DLLs are not kept in git. By default they come from the runtime (default ..\AVEVA_Nexus_local):
  versions\<first line of current.txt>\, falling back to bin\. Pass -DllDir to use another folder.
  The output folder is <Out>\NexusMCP-<version>, version = the version folder name (or the DLL product version).
#>
param(
    [string]$Runtime = (Join-Path (Split-Path -Parent $PSScriptRoot | Split-Path -Parent) 'AVEVA_Nexus_local'),
    [string]$DllDir,
    [string]$Out
)

$ErrorActionPreference = 'Stop'
if (-not $Out) { $Out = Join-Path $Runtime 'package' }
$version = $null
if (-not $DllDir) {
    $curFile = Join-Path $Runtime 'current.txt'
    $cur = if (Test-Path $curFile) { Get-Content $curFile -TotalCount 1 } else { $null }
    if ($cur -and $cur.Trim()) {
        $version = $cur.Trim()
        $DllDir = Join-Path $Runtime "versions\$version"
    } else {
        $DllDir = Join-Path $Runtime 'bin'
    }
}
$src = Join-Path (Split-Path -Parent $PSScriptRoot) 'pmllib\NexusMCP'

foreach ($name in 'AVEVA_Nexux_Addin.dll', 'Newtonsoft.Json.dll') {
    if (-not (Test-Path (Join-Path $DllDir $name))) { throw "Missing $name in $DllDir" }
}
if (-not $version) { $version = (Get-Item (Join-Path $DllDir 'AVEVA_Nexux_Addin.dll')).VersionInfo.ProductVersion }
$suffix = if ($version) { '-' + ($version -replace '[^A-Za-z0-9._+-]', '_') } else { '' }
$dest = Join-Path $Out "NexusMCP$suffix"

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
