@echo off
chcp 65001 >nul
cd /d "%~dp0..\..\.."
title KEO node index progress
.\.venv\Scripts\python.exe experiments\KEO\tools\watch_progress.py
echo.
pause
