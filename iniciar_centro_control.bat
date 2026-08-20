@echo off
setlocal
cd /d "%~dp0"
title Arzyz Vision - Centro de Control
set "ARZYZ_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%ARZYZ_PYTHON%" set "ARZYZ_PYTHON=python"
"%ARZYZ_PYTHON%" centro_control.py
if errorlevel 1 (
  echo.
  echo El Centro de Control termino con un error.
  pause
)
