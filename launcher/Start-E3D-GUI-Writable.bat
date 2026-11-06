@echo off
rem Opens graphical E3D (-Project <code>, or default_gui_project in config\runtime.json) with Nexus write access.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-E3D-GUI-Writable.ps1" %*
if errorlevel 1 pause
