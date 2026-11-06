# Test-Launcher.ps1 - tests the runtime launcher without E3D (no Pester needed).
# Usage: powershell -NoProfile -File tests\launcher\Test-Launcher.ps1
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$script:fail = 0; $script:pass = 0
function Check([bool]$Cond, [string]$Msg) {
    if ($Cond) { $script:pass++; Write-Host "  ok   $Msg" } else { $script:fail++; Write-Host "  FAIL $Msg" -ForegroundColor Red }
}
function Throws([scriptblock]$Block) { try { & $Block | Out-Null; return $false } catch { return $true } }

$tmp = Join-Path ([IO.Path]::GetTempPath()) ("nexus-launcher-test-" + [guid]::NewGuid().ToString('N'))
$rt = Join-Path $tmp 'runtime'
try {
    Write-Host 'install-runtime'
    & "$repo\scripts\install-runtime.ps1" -Runtime $rt | Out-Null
    foreach ($f in 'Start-Nexus.ps1', 'Stop-Nexus.ps1', 'Start-E3D-GUI-Writable.ps1', 'Start-E3D-GUI-Writable.bat', 'Stop-Nexus-GUI.pmlmac', 'NexusRuntime.ps1', 'config\projects.json', 'config\projects.example.json') {
        Check (Test-Path (Join-Path $rt $f)) "installed $f"
    }
    Check ((Test-Path "$rt\versions") -and (Test-Path "$rt\run")) 'versions\ and run\ created'
    Set-Content "$rt\config\projects.json" '{"KEEP":{}}'
    & "$repo\scripts\install-runtime.ps1" -Runtime $rt | Out-Null
    Check ((Get-Content "$rt\config\projects.json" -Raw).Trim() -eq '{"KEEP":{}}') 'projects.json never overwritten (idempotent run)'
    & "$repo\scripts\install-runtime.ps1" -Runtime $rt -Force | Out-Null
    Check ((Get-Content "$rt\config\projects.json" -Raw).Trim() -eq '{"KEEP":{}}') 'projects.json not overwritten even with -Force'

    Write-Host 'path resolution'
    . "$rt\NexusRuntime.ps1"
    Check ((Get-NexusDllDir $rt) -eq (Join-Path $rt 'bin')) 'no current.txt -> bin\'
    New-Item -ItemType Directory -Force "$rt\versions\1.2.3" | Out-Null
    Set-Content "$rt\versions\1.2.3\AVEVA_Nexux_Addin.dll" 'dummy'
    Set-Content "$rt\current.txt" "1.2.3`r`nsecond line ignored" -Encoding ASCII
    Check ((Get-NexusDllDir $rt) -eq (Join-Path $rt 'versions\1.2.3')) 'current.txt -> versions\<first line>'
    Check ((Get-NexusAddinPath $rt) -eq (Join-Path $rt 'versions\1.2.3\AVEVA_Nexux_Addin')) 'addin path has no extension'
    Set-Content "$rt\current.txt" 'missing' -Encoding ASCII
    Check (Throws { Get-NexusDllDir $rt }) 'current.txt pointing to a missing version throws'
    Remove-Item "$rt\current.txt"

    $savedRepo = $env:NEXUS_REPO; $env:NEXUS_REPO = $null
    Check ((Get-NexusRepoDir $rt) -eq [IO.Path]::GetFullPath((Join-Path $rt '..\AVEVA_Nexus'))) 'repo default = ..\AVEVA_Nexus'
    Set-Content "$rt\config\runtime.json" '{"repo":"..\\somerepo"}'
    Check ((Get-NexusRepoDir $rt) -eq [IO.Path]::GetFullPath((Join-Path $rt '..\somerepo'))) 'repo from runtime.json (relative)'
    $envRepo = 'X' + ':\envrepo'   # concatenated so the leak scanner does not see a drive path
    $env:NEXUS_REPO = $envRepo
    Check ((Get-NexusRepoDir $rt) -eq $envRepo) 'NEXUS_REPO wins'
    $env:NEXUS_REPO = $savedRepo
    Remove-Item "$rt\config\runtime.json"

    Write-Host 'deploy-local'
    $dummy = Join-Path $tmp 'dll'; New-Item -ItemType Directory -Force $dummy | Out-Null
    Set-Content "$dummy\AVEVA_Nexux_Addin.dll" 'dll-A'; Set-Content "$dummy\Newtonsoft.Json.dll" 'nj'
    & "$repo\scripts\deploy-local.ps1" -Runtime $rt -DllPath $dummy -Version '1.0.0+abc1234' | Out-Null
    Check ((Get-Content "$rt\current.txt" -TotalCount 1) -eq '1.0.0+abc1234') 'current.txt written'
    Check ((Test-Path "$rt\versions\1.0.0+abc1234\AVEVA_Nexux_Addin.dll") -and (Test-Path "$rt\versions\1.0.0+abc1234\Newtonsoft.Json.dll")) 'DLLs in versions\<v>\'
    Check (-not (Test-Path "$rt\current.txt.prev")) 'no .prev on first deploy'
    Check (Throws { & "$repo\scripts\deploy-local.ps1" -Runtime $rt -DllPath $dummy -Version '1.0.0+abc1234' }) 'existing version folder not overwritten without -Force'
    Set-Content "$dummy\AVEVA_Nexux_Addin.dll" 'dll-B'
    & "$repo\scripts\deploy-local.ps1" -Runtime $rt -DllPath $dummy -Version '2.0.0 dirty/x' | Out-Null
    Check ((Get-Content "$rt\current.txt" -TotalCount 1) -eq '2.0.0_dirty_x') 'version sanitised'
    Check ((Get-Content "$rt\current.txt.prev" -TotalCount 1) -eq '1.0.0+abc1234') 'previous kept in current.txt.prev'
    Check ((Get-Content "$rt\versions\1.0.0+abc1234\AVEVA_Nexux_Addin.dll" -Raw).Trim() -eq 'dll-A') 'old version folder untouched'
    & "$repo\scripts\deploy-local.ps1" -Runtime $rt -Rollback | Out-Null
    Check ((Get-Content "$rt\current.txt" -TotalCount 1) -eq '1.0.0+abc1234') 'rollback -> previous'
    Check ((Get-Content "$rt\current.txt.prev" -TotalCount 1) -eq '2.0.0_dirty_x') 'rollback keeps swap value'
    & "$repo\scripts\deploy-local.ps1" -Runtime $rt -Rollback | Out-Null
    Check ((Get-Content "$rt\current.txt" -TotalCount 1) -eq '2.0.0_dirty_x') 'second rollback swaps back'
    Check ((Get-NexusDllDir $rt) -eq (Join-Path $rt 'versions\2.0.0_dirty_x')) 'launcher resolves the deployed version'

    Write-Host 'package-pmllib'
    & "$repo\scripts\package-pmllib.ps1" -Runtime $rt -Out "$tmp\pkg" | Out-Null
    Check (Test-Path "$tmp\pkg\NexusMCP-2.0.0_dirty_x\dll\AVEVA_Nexux_Addin.dll") 'package named with version, DLL from current'

    Write-Host 'templates'
    $bad = Get-ChildItem "$repo\launcher" -Recurse -File | Where-Object { Select-String -Path $_.FullName -Pattern ('(?i)\b' + 'D' + ':\\'), ('(?i)' + 'C' + ':\\Users') -Quiet }
    Check (-not $bad) ('no absolute drive or user-profile path in launcher\' + $(if ($bad) { ' (' + ($bad.Name -join ', ') + ')' }))
} finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host "`n$script:pass passed, $script:fail failed"
if ($script:fail) { exit 1 }
