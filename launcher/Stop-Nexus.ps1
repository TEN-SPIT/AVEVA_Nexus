param([Parameter(Mandatory = $true)][string]$Project)
# mon.exe spawns des.exe (-tty) and exits, so stop both: the tracked mon PID and any TTY des.exe.
# The session also leaves orphan PDMSConsole.exe helpers that keep stdout.log locked; those are killed too, but only
# orphans (parent process gone) created after the launch, so GUI E3D sessions of the user are never touched.
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $root "run\$Project\e3d.pid"
$launch = if (Test-Path $pidFile) { (Get-Item $pidFile).LastWriteTime } else { (Get-Date).AddHours(-12) }

if (Test-Path $pidFile) {
    $id = [int](Get-Content $pidFile)
    if (Get-Process -Id $id -ErrorAction SilentlyContinue) { taskkill /PID $id /T /F | Out-Null; "Stopped launcher PID $id" }
    Remove-Item $pidFile -Force
}

Get-CimInstance Win32_Process -Filter "Name='des.exe'" |
    Where-Object { $_.CommandLine -match '-tty' } |
    ForEach-Object { taskkill /PID $_.ProcessId /T /F | Out-Null; "Stopped des.exe (TTY) PID $($_.ProcessId)" }

Get-CimInstance Win32_Process -Filter "Name='PDMSConsole.exe'" |
    Where-Object { $_.CreationDate -ge $launch -and -not (Get-Process -Id $_.ParentProcessId -ErrorAction SilentlyContinue) } |
    ForEach-Object { taskkill /PID $_.ProcessId /F | Out-Null; "Stopped orphan PDMSConsole PID $($_.ProcessId)" }
