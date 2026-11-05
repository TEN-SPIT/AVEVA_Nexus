@echo off
rem Opens graphical E3D (-Project <code>, or default_gui_project in config\runtime.json) in the normal
rem read-only mode, with the "Nexus MCP" panel: start/stop Nexus and switch Lectura / Edicion from there.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-E3D-GUI-Writable.ps1" -Panel -ReadOnly %*
if errorlevel 1 pause
