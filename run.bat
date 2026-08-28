@echo off
REM PI-SEQ launcher (Windows)
cd /d %~dp0
.venv\Scripts\python run.py %*
