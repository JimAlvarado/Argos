@echo off
setlocal
cd /d "%~dp0"
set "ARZYZ_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%ARZYZ_PYTHON%" set "ARZYZ_PYTHON=python"
echo.
echo  Generando diagnostico de Arzyz Vision...
echo.
"%ARZYZ_PYTHON%" -m tools.diagnostico
if errorlevel 1 (
  echo.
  echo  El diagnostico termino con un error. Copia el texto de arriba.
  pause
  exit /b 1
)
echo.
echo  Abriendo la carpeta del reporte...
if exist "%~dp0data\diagnostico" start "" "%~dp0data\diagnostico"
echo.
echo  Comparte el archivo .txt mas reciente.
pause
