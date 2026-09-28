@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
set "NARAKA_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%NARAKA_PYTHON%" set "NARAKA_PYTHON=D:\NarakaCheckinAssistant\.venv\Scripts\python.exe"
if not exist "%NARAKA_PYTHON%" (
  echo Run Setup.ps1 first.
  pause
  exit /b 1
)
"%NARAKA_PYTHON%" -m unittest discover -s tests -v
exit /b %errorlevel%
