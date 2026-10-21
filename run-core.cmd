@echo off
cd /d "%~dp0"
py scripts\launch_web.py %*
if errorlevel 1 pause
