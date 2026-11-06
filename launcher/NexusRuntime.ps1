# NexusRuntime.ps1 - helpers shared by the runtime scripts (dot-source: . (Join-Path $root 'NexusRuntime.ps1')).
# The runtime root is the folder of the calling script; nothing here depends on an absolute machine path.

function Get-NexusDllDir {
    # <root>\versions\<first line of current.txt>\ ; falls back to <root>\bin\ when current.txt is missing or empty.
    param([Parameter(Mandatory = $true)][string]$Root)
    $cur = Join-Path $Root 'current.txt'
    if (Test-Path $cur) {
        $ver = Get-Content $cur -TotalCount 1
        if ($ver) { $ver = $ver.Trim() }
        if ($ver) {
            $dir = Join-Path $Root "versions\$ver"
            if (-not (Test-Path (Join-Path $dir 'AVEVA_Nexux_Addin.dll'))) {
                throw "current.txt points to '$ver' but $dir has no AVEVA_Nexux_Addin.dll"
            }
            return $dir
        }
    }
    return (Join-Path $Root 'bin')
}

function Get-NexusRepoDir {
    # $env:NEXUS_REPO, else "repo" in config\runtime.json (absolute, or relative to the runtime root),
    # else the old layout: ..\AVEVA_Nexus beside the runtime.
    param([Parameter(Mandatory = $true)][string]$Root)
    if ($env:NEXUS_REPO) { return $env:NEXUS_REPO }
    $rc = Join-Path $Root 'config\runtime.json'
    if (Test-Path $rc) {
        $j = Get-Content $rc -Raw | ConvertFrom-Json
        if ($j.repo) {
            if ([IO.Path]::IsPathRooted($j.repo)) { return $j.repo }
            return [IO.Path]::GetFullPath((Join-Path $Root $j.repo))
        }
    }
    return [IO.Path]::GetFullPath((Join-Path $Root '..\AVEVA_Nexus'))
}

function Get-NexusAddinPath {
    # Path without extension, as the PML 'import' command expects it.
    param([Parameter(Mandatory = $true)][string]$Root)
    return (Join-Path (Get-NexusDllDir $Root) 'AVEVA_Nexux_Addin')
}
