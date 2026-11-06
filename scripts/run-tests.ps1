# Unit tests: compiles tests\Nexus.Tests\*.cs together with every AVEVA-free source of the add-in (services,
# controllers, Router, the provider seam...) and runs them against the in-memory FakeProvider. No E3D, no Aveva DLLs.
#
# Env:     NEXUS_CSC         Roslyn csc.exe (else vswhere's newest VS/BuildTools, else the default BuildTools path)
#          NEXUS_NEWTONSOFT  Newtonsoft.Json.dll (else AVEVA_Nexus\packages\Newtonsoft.Json.13.0.1\lib\net45)
# Output:  out\Nexus.Tests.exe, out\test-results.xml (JUnit), console summary "N passed, M failed".
# Exit:    0 when every check passed; != 0 on a build failure or any failed check.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$out = Join-Path $repo 'out'
$src = Join-Path $repo 'AVEVA_Nexus\AVEVA_Nexus'

# Roslyn csc, found like scripts\build.ps1 does.
$csc = $env:NEXUS_CSC
if (-not $csc) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path $vswhere) {
        $vs = & $vswhere -latest -products * -requires Microsoft.Component.MSBuild -property installationPath
        if ($vs) { $csc = Join-Path $vs 'MSBuild\Current\Bin\Roslyn\csc.exe' }
    }
}
if (-not $csc -or -not (Test-Path $csc)) { $csc = 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\Roslyn\csc.exe' }
if (-not (Test-Path $csc)) { Write-Error "Roslyn csc.exe not found: $csc (set NEXUS_CSC)"; exit 2 }

$newtonsoft = $env:NEXUS_NEWTONSOFT
if (-not $newtonsoft) { $newtonsoft = Join-Path $repo 'AVEVA_Nexus\packages\Newtonsoft.Json.13.0.1\lib\net45\Newtonsoft.Json.dll' }
if (-not (Test-Path $newtonsoft)) { Write-Error "Newtonsoft.Json.dll not found: $newtonsoft (set NEXUS_NEWTONSOFT)"; exit 2 }
New-Item -ItemType Directory -Force $out | Out-Null

# Everything except the files that really need Aveva.* (and the add-in's assembly attributes). New files are
# picked up automatically; one that references Aveva.* makes this build fail, which is the point.
$excluded = @('Addin\', 'Config\HostConfig.cs', 'Integration\AvevaProvider.cs', 'Properties\AssemblyInfo.cs')
$pure = Get-ChildItem $src -Recurse -Filter *.cs |
    Where-Object { $_.FullName -notmatch '\\(obj|bin)\\' } |
    Where-Object {
        $rel = $_.FullName.Substring($src.Length + 1)
        -not ($excluded | Where-Object { $rel -eq $_ -or ($_.EndsWith('\') -and $rel.StartsWith($_)) })
    } |
    ForEach-Object { $_.FullName }
$tests = (Get-ChildItem (Join-Path $repo 'tests\Nexus.Tests') -Filter *.cs).FullName

$exe = Join-Path $out 'Nexus.Tests.exe'
$files = (@($pure) + @($tests)) | ForEach-Object { '"' + $_ + '"' }
$rsp = Join-Path $out 'tests.rsp'
@('/nologo', '/target:exe', '/platform:x86', "/out:`"$exe`"", "/r:`"$newtonsoft`"",
  '/r:System.Net.Http.dll', '/r:System.Xml.Linq.dll') + $files | Set-Content $rsp -Encoding ASCII
& $csc "@$rsp"
if ($LASTEXITCODE -ne 0) { Write-Error "Test build failed (csc exit $LASTEXITCODE)"; exit 1 }
Copy-Item $newtonsoft $out -Force

& $exe --junit (Join-Path $out 'test-results.xml')
exit $LASTEXITCODE
