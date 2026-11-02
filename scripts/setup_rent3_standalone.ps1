# scripts/setup_rent3_standalone.ps1
# BaseSaasApp에서 rent3 패밀리(5개 앱 + _rent3_core + 전용 스킬 2종)만 연결한
# 초경량 실시간 연동 워크스페이스(Rent3_Standalone)를 생성합니다.
# 원본과 디렉터리 정션(Junction) 및 하드링크로 연결되므로,
# Rent3_Standalone에서 수정한 코드와 스킬은 BaseSaasApp 본체에 실시간 100% 자동 반영됩니다.

param(
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$TargetRoot = "C:\Users\USER\Desktop\Workstation\Rent3_Standalone"
)

$ErrorActionPreference = "Stop"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "🚀 Rent3_Standalone 경량 워크스페이스(실시간 정션 연동) 구성" -ForegroundColor Cyan
Write-Host "• 원본 (BaseSaasApp) : $SourceRoot"
Write-Host "• 대상 (Standalone)  : $TargetRoot"
Write-Host "============================================================" -ForegroundColor Cyan

# 1. 기본 디렉터리 골격 생성
$baseDirs = @(
    $TargetRoot,
    (Join-Path $TargetRoot "apps"),
    (Join-Path $TargetRoot ".agents"),
    (Join-Path $TargetRoot ".agents\skills"),
    (Join-Path $TargetRoot "data"),
    (Join-Path $TargetRoot "tests"),
    (Join-Path $TargetRoot "scripts")
)

foreach ($d in $baseDirs) {
    if (-not (Test-Path $d)) {
        New-Item -ItemType Directory -Path $d | Out-Null
    }
}

# 2. 디렉터리 정션(Junction, mklink /J) 연결 함수
function Ensure-Junction {
    param([string]$LinkPath, [string]$TargetPath)
    if (-not (Test-Path $TargetPath)) {
        Write-Warning "원본 경로가 존재하지 않아 건너뜁니다: $TargetPath"
        return
    }
    if (Test-Path $LinkPath) {
        Write-Host "  [유지] 이미 연결됨: $LinkPath" -ForegroundColor DarkGray
        return
    }
    cmd /c mklink /J "$LinkPath" "$TargetPath" | Out-Null
    Write-Host "  [연결] $LinkPath -> $TargetPath" -ForegroundColor Green
}

# 3. 단일 파일 하드링크(HardLink, mklink /H) 연결 함수
function Ensure-HardLink {
    param([string]$LinkPath, [string]$TargetPath)
    if (-not (Test-Path $TargetPath)) {
        Write-Warning "원본 파일이 존재하지 않아 건너뜁니다: $TargetPath"
        return
    }
    if (Test-Path $LinkPath) {
        Write-Host "  [유지] 이미 연결됨: $LinkPath" -ForegroundColor DarkGray
        return
    }
    cmd /c mklink /H "$LinkPath" "$TargetPath" | Out-Null
    Write-Host "  [파일연결] $LinkPath -> $TargetPath" -ForegroundColor Green
}

# ── (A) 플랫폼 불변 코어 연결 ──
Ensure-Junction (Join-Path $TargetRoot "core")      (Join-Path $SourceRoot "core")
Ensure-Junction (Join-Path $TargetRoot "shared_ui") (Join-Path $SourceRoot "shared_ui")
Ensure-Junction (Join-Path $TargetRoot "gateway")   (Join-Path $SourceRoot "gateway")
Ensure-HardLink (Join-Path $TargetRoot "run.py")    (Join-Path $SourceRoot "run.py")

# ── (B) rent3 전용 Skill 2종만 선택 연결 (타 도메인 스킬 배제) ──
Ensure-Junction (Join-Path $TargetRoot ".agents\skills\basesaasapp-governor") (Join-Path $SourceRoot ".agents\skills\basesaasapp-governor")
Ensure-Junction (Join-Path $TargetRoot ".agents\skills\bass-rent3-ops")       (Join-Path $SourceRoot ".agents\skills\bass-rent3-ops")

# ── (C) rent3 패밀리 6개 폴더만 apps/ 에 연결 (타 앱 배제) ──
$rent3Apps = @(
    "_rent3_core",
    "rent3",
    "rent3_admin",
    "rent3_lessor",
    "rent3_tenant",
    "rent3_contract"
)
foreach ($appName in $rent3Apps) {
    Ensure-Junction (Join-Path $TargetRoot "apps\$appName") (Join-Path $SourceRoot "apps\$appName")
}

# ── (D) 실시간 공동 JSON Data Pool 연결 ──
Ensure-Junction (Join-Path $TargetRoot "data\rent3_pool") (Join-Path $SourceRoot "data\rent3_pool")

# ── (E) rent3 전용 테스트 및 실행 배치/스크립트 연결 ──
Ensure-HardLink (Join-Path $TargetRoot "run_r3.bat")                       (Join-Path $SourceRoot "run_r3.bat")
Ensure-HardLink (Join-Path $TargetRoot "test_r3.bat")                      (Join-Path $SourceRoot "test_r3.bat")
Ensure-HardLink (Join-Path $TargetRoot "tests\test_rent3_pool_suite.py")   (Join-Path $SourceRoot "tests\test_rent3_pool_suite.py")
Ensure-HardLink (Join-Path $TargetRoot "scripts\generate_rent3_qa_pdf.py") (Join-Path $SourceRoot "scripts\generate_rent3_qa_pdf.py")

Write-Host ""
Write-Host "✅ Rent3_Standalone 구성 완료!" -ForegroundColor Green
Write-Host "1. Antigravity IDE에서 [File -> Open Folder] 로 아래 폴더를 여세요:" -ForegroundColor Yellow
Write-Host "   $TargetRoot" -ForegroundColor White
Write-Host "2. 단독 개발 서버 실행: py run.py --port 8080" -ForegroundColor Yellow
Write-Host "3. 단독 테스트 실행  : py -m unittest tests/test_rent3_pool_suite.py -v" -ForegroundColor Yellow
