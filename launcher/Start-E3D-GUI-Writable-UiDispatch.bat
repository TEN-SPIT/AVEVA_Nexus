@echo off
rem Same as Start-E3D-GUI-Writable.bat, plus NEXUS_UI_DISPATCH=1 (Nexus runs E3D work on the user-interface thread).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-E3D-GUI-Writable.ps1" -UiDispatch %*
if errorlevel 1 pause
