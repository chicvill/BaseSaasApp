@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

:: Rent3_Standalone 폴더가 존재하면 해당 경량 워크스페이스 기준으로 단독 테스트
set TARGET_DIR=C:\Users\USER\Desktop\Workstation\Rent3_Standalone
if not exist "%TARGET_DIR%\run.py" (
    set TARGET_DIR=%~dp0
)

cd /d "%TARGET_DIR%"

echo ============================================================
echo 🧪 Rent3 단독 통합 테스트 스위트 실행
echo • 작업 경로 : %CD%
echo ============================================================

py -m unittest tests/test_rent3_pool_suite.py apps/rent3/tests/test_api_routers.py -v
set EXIT_CODE=%ERRORLEVEL%

echo ============================================================
if %EXIT_CODE% EQU 0 (
    echo ✅ 모든 Rent3 테스트가 성공적으로 통과했습니다!
) else (
    echo ❌ 일부 테스트가 실패했습니다. (종료 코드: %EXIT_CODE%)
)
echo ============================================================

:: 탐색기에서 더블클릭으로 실행한 경우에만 창이 바로 닫히지 않도록 대기
echo %CMDCMDLINE% | find /i "/c" >nul
if not errorlevel 1 (
    if "%1"=="" pause
)

exit /b %EXIT_CODE%
