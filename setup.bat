@echo off
REM PI-SEQ Windows setup: create venv + install dependencies
cd /d %~dp0
python -m venv .venv
.venv\Scripts\pip install --upgrade pip
.venv\Scripts\pip install -r requirements.txt
echo.
echo ==============================================
echo  setup done! run with: run.bat
echo  (e.g. run.bat --no-virtual)
echo ==============================================
pause
