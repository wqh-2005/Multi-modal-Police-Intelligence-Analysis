@echo off
chcp 65001 >nul
cd /d "%~dp0..\..\.."
title KEO 节点向量化进度
echo 正在读取进度，每 5 秒刷新；按 Ctrl+C 退出。
echo.
.\.venv\Scripts\python.exe experiments\KEO\tools\watch_progress.py
echo.
pause
