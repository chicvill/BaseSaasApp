@echo off
setlocal
cd /d "%~dp0"

REM -----------------------------------------------------------
REM BaseSaasApp Server Launcher
REM -----------------------------------------------------------

set "PYTHON_CMD="

REM 1. Check Python Launcher for 3.14
py -3.14 --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=py -3.14"
    goto FOUND
)

REM 2. Check LocalAppData Python 3.14
if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" (
    set "PYTHON_CMD="%LOCALAPPDATA%\Programs\Python\Python314\python.exe""
    goto FOUND
)

REM 3. Check LocalAppData Python 3.13
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" (
    set "PYTHON_CMD="%LOCALAPPDATA%\Programs\Python\Python313\python.exe""
    goto FOUND
)

REM 4. Check Generic Python Launcher
py --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=py"
    goto FOUND
)

REM 5. Check PATH Python
python --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=python"
    goto FOUND
)

echo ============================================================
echo [ERROR] Python executable not found!
echo Please make sure Python 3.13 or 3.14 is installed.
echo ============================================================
pause
exit /b 1

:FOUND
%PYTHON_CMD% run.py %*

if errorlevel 1 (
    echo.
    echo [Notice] Server exited with error code %errorlevel%.
    pause
)
