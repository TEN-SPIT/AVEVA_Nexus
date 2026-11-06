<#
.SYNOPSIS
  Installs a local git pre-push hook that runs the sensitive-data scanner on the commits being pushed.
.DESCRIPTION
  Project-specific forbidden words go (one regex per line) in the untracked .sensitive-patterns file
  at the repo root. Re-run this script to update the hook; use `git push --no-verify` to bypass.
#>
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim()
$hooks = Join-Path $root ((git -C $root rev-parse --git-path hooks).Trim())
if (-not [System.IO.Path]::IsPathRooted($hooks)) { $hooks = Join-Path $root (git -C $root rev-parse --git-path hooks).Trim() }
New-Item -ItemType Directory -Force $hooks | Out-Null

$hook = @'
#!/bin/sh
# pre-push: scan the commits about to be pushed (installed by .github/scripts/install-hooks.ps1)
ROOT=$(git rev-parse --show-toplevel)
PY=$(command -v python3 || command -v python || command -v py)
[ -n "$PY" ] || { echo "pre-push: python not found, scan skipped" >&2; exit 0; }
ZERO=0000000000000000000000000000000000000000
status=0
while read -r local_ref local_sha remote_ref remote_sha; do
  [ "$local_sha" = "$ZERO" ] && continue
  if [ "$remote_sha" = "$ZERO" ]; then
    range="$local_sha --not --remotes"
  else
    range="$remote_sha..$local_sha"
  fi
  "$PY" -I "$ROOT/.github/scripts/scan_sensitive.py" --range "$range" || status=1
done
if [ $status -ne 0 ]; then
  echo "pre-push: sensitive data found in the commits above; push aborted (git push --no-verify to override)." >&2
fi
exit $status
'@
$target = Join-Path $hooks 'pre-push'
[System.IO.File]::WriteAllText($target, ($hook -replace "`r`n", "`n"), (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Installed $target"
