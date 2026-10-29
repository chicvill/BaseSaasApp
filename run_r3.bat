@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

set PORT=%1
if "%PORT%"=="" set PORT=8080

set TARGET_DIR=C:\Users\USER\Desktop\Workstation\Rent3_Standalone
if not exist "%TARGET_DIR%\run.py" set TARGET_DIR=%~dp0

cd /d "%TARGET_DIR%"

echo ============================================================
echo [Rent3 Standalone Server] Port: %PORT%
echo WorkDir  : %CD%
echo Portal   : http://localhost:%PORT%/rent3/index.html
echo Admin    : http://localhost:%PORT%/rent3_admin/
echo Lessor   : http://localhost:%PORT%/rent3_lessor/
echo Tenant   : http://localhost:%PORT%/rent3_tenant/
echo Contract : http://localhost:%PORT%/rent3_contract/
echo ============================================================

py run.py --port %PORT%

if errorlevel 1 (
    echo.
    echo [ERROR] Server exited with an error.
    pause
)
