@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

set TARGET_DIR=C:\Users\USER\Desktop\Workstation\Rent3_Standalone
if not exist "%TARGET_DIR%\run.py" set TARGET_DIR=%~dp0

cd /d "%TARGET_DIR%"

echo ============================================================
echo [Rent3 Standalone Test Suite]
echo WorkDir : %CD%
echo ============================================================

py -m unittest tests/test_rent3_pool_suite.py apps/rent3/tests/test_api_routers.py -v
set EXIT_CODE=%ERRORLEVEL%

echo ============================================================
if %EXIT_CODE% EQU 0 (
    echo [PASS] All Rent3 standalone tests passed!
) else (
    echo [FAIL] Some tests failed. Exit code: %EXIT_CODE%
)
echo ============================================================

if /i "%1"=="--pause" pause
exit /b %EXIT_CODE%
