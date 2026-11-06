# Builds AVEVA_Nexux_Addin.dll with the Roslyn csc.exe from VS BuildTools (the .NET Framework csc is C# 5 and cannot
# compile this code). Output: <Out>\AVEVA_Nexux_Addin.dll (+ Newtonsoft.Json.dll next to it).
param(
    [string]$E3dDir = 'C:\Program Files (x86)\AVEVA\Everything3D3.1',
    [string]$Out
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
if (-not $Out) { $Out = Join-Path $repo 'out' }
# Roslyn csc: $env:NEXUS_CSC, else the newest VS/BuildTools found by vswhere, else the default BuildTools path.
$csc = $env:NEXUS_CSC
if (-not $csc) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path $vswhere) {
        $vs = & $vswhere -latest -products * -requires Microsoft.Component.MSBuild -property installationPath
        if ($vs) { $csc = Join-Path $vs 'MSBuild\Current\Bin\Roslyn\csc.exe' }
    }
}
if (-not $csc -or -not (Test-Path $csc)) { $csc = 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\Roslyn\csc.exe' }
if (-not (Test-Path $csc)) { throw "Roslyn csc.exe not found: $csc (set NEXUS_CSC)" }

$src = Join-Path $repo 'AVEVA_Nexus\AVEVA_Nexus'
# $env:NEXUS_NEWTONSOFT overrides the git-ignored package folder (CI, worktrees), as in run-tests.ps1.
$newtonsoft = if ($env:NEXUS_NEWTONSOFT) { $env:NEXUS_NEWTONSOFT } else { Join-Path $repo 'AVEVA_Nexus\packages\Newtonsoft.Json.13.0.1\lib\net45\Newtonsoft.Json.dll' }
New-Item -ItemType Directory -Force $Out | Out-Null

$files = Get-ChildItem $src -Recurse -Filter *.cs | Where-Object { $_.FullName -notmatch '\\(obj|bin)\\' } | ForEach-Object { '"' + $_.FullName + '"' }

# Version stamp: VERSION file + git commit, shown by GET /api/Health ("1.1.0+abc1234", "-dirty" with uncommitted changes).
$version = (Get-Content (Join-Path $repo 'VERSION') -TotalCount 1).Trim()
$sha = (& git -C $repo rev-parse --short HEAD 2>$null)
if ($sha) {
    $version += "+$sha"
    if (& git -C $repo status --porcelain 2>$null) { $version += '-dirty' }
}
$buildInfo = Join-Path $Out 'BuildInfo.cs'
"[assembly: System.Reflection.AssemblyInformationalVersion(`"$version`")]" | Set-Content $buildInfo -Encoding ASCII
$files = @($files) + ('"' + $buildInfo + '"')
$rsp = Join-Path $Out 'build.rsp'
$refs = 'Aveva.ApplicationFramework', 'Aveva.Core.Commands', 'Aveva.Core.Database', 'Aveva.Core.Utilities', 'PMLNet' |
    ForEach-Object { '/r:"' + (Join-Path $E3dDir "$_.dll") + '"' }
@(
    '/nologo', '/target:library', '/platform:x86', "/out:`"$(Join-Path $Out 'AVEVA_Nexux_Addin.dll')`"",
    "/r:`"$newtonsoft`"", '/r:System.Configuration.dll', '/r:System.Net.Http.dll'
) + $refs + $files | Set-Content $rsp -Encoding ASCII

& $csc "@$rsp"
if ($LASTEXITCODE -ne 0) { Write-Error "Build failed (csc exit $LASTEXITCODE)"; exit $LASTEXITCODE }
Copy-Item $newtonsoft $Out -Force
"Built $(Join-Path $Out 'AVEVA_Nexux_Addin.dll') ($version)"
