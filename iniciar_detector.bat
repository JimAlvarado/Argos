@echo off
setlocal
cd /d "%~dp0"
set "ARZYZ_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%ARZYZ_PYTHON%" set "ARZYZ_PYTHON=python"
"%ARZYZ_PYTHON%" detector_empresarial.py
if errorlevel 1 (
  echo.
  echo El detector termino con un error.
  pause
)
