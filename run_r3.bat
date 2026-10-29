@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

:: 포트 설정 (첫 번째 인자로 포트 지정 가능, 기본값 8080)
set PORT=%1
if "%PORT%"=="" set PORT=8080

:: Rent3_Standalone 폴더가 존재하면 해당 경량 워크스페이스 기준으로 단독 구동
set TARGET_DIR=C:\Users\USER\Desktop\Workstation\Rent3_Standalone
if not exist "%TARGET_DIR%\run.py" (
    set TARGET_DIR=%~dp0
)

cd /d "%TARGET_DIR%"

echo ============================================================
echo 🚀 Rent3 단독 개발 서버 기동 (포트: %PORT%)
echo • 작업 경로 : %CD%
echo • 직거래 포털 : http://localhost:%PORT%/rent3/index.html
echo • 관리인 앱   : http://localhost:%PORT%/rent3_admin/
echo • 임대인 앱   : http://localhost:%PORT%/rent3_lessor/
echo • 임차인 앱   : http://localhost:%PORT%/rent3_tenant/
echo • 공동계약 앱 : http://localhost:%PORT%/rent3_contract/
echo ============================================================

py run.py --port %PORT%

if errorlevel 1 (
    echo.
    echo [오류] 서버가 비정상 종료되었습니다.
    pause
)
