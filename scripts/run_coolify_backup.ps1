<#
.SYNOPSIS
  Coolify Rent3 백업(.tar.gz)을 로컬 비공개 폴더(data\private\coolify_live)에 안전하게 복구하고 서버를 실행합니다.

.DESCRIPTION
  - 기본 시연 데이터(data\rent3_pool)는 건드리지 않고, Git 제외 폴더(data\private\coolify_live)만 사용합니다.
  - 백업 파일을 주면 먼저 무결성 검증(--verify)과 임시 폴더 복구를 통과한 뒤 기존 폴더와 교체합니다.
  - data\private\coolify.env 파일이 있으면 암호화 키(MASTER_CRYPTO_KEY 등)를 자동으로 불러옵니다.

.EXAMPLE
  # 1) 이미 복구해 둔 data\private\coolify_live 로 바로 서버 실행
  .\scripts\run_coolify_backup.ps1

  # 2) 특정 백업 파일로 이전 폴더 교체 복구 후 서버 실행
  .\scripts\run_coolify_backup.ps1 "C:\Users\USER\Downloads\rent3_pool_20261127_120000.tar.gz"

  # 3) 다운로드 폴더의 가장 최신 rent3_pool_*.tar.gz 자동 탐색 -> 복구 -> 서버 실행
  .\scripts\run_coolify_backup.ps1 -Latest

  # 4) 서버는 띄우지 않고 복구만 실행
  .\scripts\run_coolify_backup.ps1 -Latest -RestoreOnly
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$BackupFile = "",

    [switch]$Latest,

    [switch]$RestoreOnly,

    [string]$TargetDir = "",

    [int]$Port = 8080
)

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $PSScriptRoot
Set-Location $RootDir

if (-not $TargetDir) {
    $TargetDir = Join-Path $RootDir "data\private\coolify_live"
} else {
    $TargetDir = [System.IO.Path]::GetFullPath($TargetDir)
}

# Python 실행기 탐색 (run.bat 과 동일 우선순위)
function Get-PythonArgs {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.14 --version *> $null
        if ($LASTEXITCODE -eq 0) { return @("py", "-3.14") }
        return @("py")
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @("python")
    }
    throw "Python 실행기(py 또는 python)를 찾을 수 없습니다."
}

$PyCmd = Get-PythonArgs
function Invoke-Py {
    param([string[]]$ScriptArgs)
    if ($PyCmd.Count -gt 1) {
        & $PyCmd[0] $PyCmd[1] @ScriptArgs
    } else {
        & $PyCmd[0] @ScriptArgs
    }
}

# 선택: data\private\coolify.env 에서 암호화 키 등 로컬 전용 환경변수 자동 로드
$EnvFile = Join-Path $RootDir "data\private\coolify.env"
if (Test-Path -LiteralPath $EnvFile) {
    $loadedKeys = @()
    foreach ($raw in (Get-Content -LiteralPath $EnvFile -Encoding UTF8)) {
        $line = $raw.Trim()
        if (-not $line -or $line.StartsWith("#")) { continue }
        $idx = $line.IndexOf("=")
        if ($idx -lt 1) { continue }
        $k = $line.Substring(0, $idx).Trim()
        $v = $line.Substring($idx + 1).Trim()
        if (($v.StartsWith('"') -and $v.EndsWith('"')) -or ($v.StartsWith("'") -and $v.EndsWith("'"))) {
            $v = $v.Substring(1, $v.Length - 2)
        }
        if ($k) {
            [System.Environment]::SetEnvironmentVariable($k, $v, "Process")
            $loadedKeys += $k
        }
    }
    if ($loadedKeys.Count -gt 0) {
        Write-Host ("[ENV] data\private\coolify.env 로드됨: " + ($loadedKeys -join ", ")) -ForegroundColor Cyan
    }
}

# -Latest 지정 시 Downloads 폴더에서 가장 최신 rent3_pool_*.tar.gz 자동 선택
if ($Latest -and -not $BackupFile) {
    $downloads = Join-Path $env:USERPROFILE "Downloads"
    $candidates = @()
    if (Test-Path -LiteralPath $downloads) {
        $candidates += Get-ChildItem -LiteralPath $downloads -Filter "rent3_pool_*.tar.gz" -File -ErrorAction SilentlyContinue
    }
    if ($candidates.Count -eq 0) {
        throw "다운로드 폴더($downloads)에서 rent3_pool_*.tar.gz 백업 파일을 찾지 못했습니다."
    }
    $picked = $candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $BackupFile = $picked.FullName
    Write-Host "[AUTO] 최신 백업 선택: $BackupFile" -ForegroundColor Cyan
}

# 백업 파일이 지정된 경우: 검증 -> 임시 폴더 복구 -> 기존 폴더 안전 교체
if ($BackupFile) {
    $BackupPath = [System.IO.Path]::GetFullPath($BackupFile)
    if (-not (Test-Path -LiteralPath $BackupPath -PathType Leaf)) {
        throw "백업 파일을 찾을 수 없습니다: $BackupPath"
    }

    Write-Host "1) 백업 무결성 검사 중: $BackupPath" -ForegroundColor Yellow
    Invoke-Py @("scripts\rent3_backup.py", "--verify", $BackupPath)
    if ($LASTEXITCODE -ne 0) {
        throw "백업 검증에 실패했습니다. 기존 폴더는 그대로 유지됩니다."
    }

    $parentDir = Split-Path -Parent $TargetDir
    if (-not (Test-Path -LiteralPath $parentDir)) {
        New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
    }
    $stagingDir = "$TargetDir`_staging_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
    try {
        Write-Host "2) 임시 폴더에 안전 복구 중: $stagingDir" -ForegroundColor Yellow
        Invoke-Py @("scripts\rent3_backup.py", "--restore", $BackupPath, "--to", $stagingDir)
        if ($LASTEXITCODE -ne 0) {
            throw "백업 복구 중 오류가 발생했습니다."
        }

        if (Test-Path -LiteralPath $TargetDir) {
            Write-Host "3) 이전 복구 폴더 삭제 중: $TargetDir" -ForegroundColor Yellow
            Remove-Item -LiteralPath $TargetDir -Recurse -Force
        }
        Move-Item -LiteralPath $stagingDir -Destination $TargetDir
        Write-Host "[OK] 복구 완료: $TargetDir" -ForegroundColor Green
    } catch {
        if (Test-Path -LiteralPath $stagingDir) {
            Remove-Item -LiteralPath $stagingDir -Recurse -Force -ErrorAction SilentlyContinue
        }
        throw
    }
}

if (-not (Test-Path -LiteralPath $TargetDir -PathType Container)) {
    throw "복구된 폴더가 없습니다: $TargetDir`n먼저 백업 파일 경로를 지정하거나 -Latest 옵션으로 실행해 주세요."
}

if ($RestoreOnly) {
    Write-Host "복구만 완료했습니다(-RestoreOnly). 서버 실행 시:" -ForegroundColor Green
    Write-Host "  .\scripts\run_coolify_backup.ps1" -ForegroundColor White
    exit 0
}

# 포트 사용 여부 사전 확인 (충돌 시 친절한 안내)
$portBusy = $false
try {
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($conns) { $portBusy = $true }
} catch {
    $portBusy = $false
}
if ($portBusy) {
    Write-Host "[주의] 포트 $Port 이 이미 사용 중입니다. 먼저 기존 실행 중인 서버 창에서 Ctrl+C 로 종료한 뒤 다시 실행해 주세요." -ForegroundColor Red
    exit 1
}

$env:RENT3_POOL_DIR = $TargetDir
$env:RENT3_DEMO_SEED = "0"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Coolify 백업 Data Pool 로 로컬 서버를 시작합니다" -ForegroundColor Cyan
Write-Host " - 데이터 경로 : $TargetDir" -ForegroundColor White
Write-Host " - 접속 주소   : http://localhost:$Port/rent3/" -ForegroundColor White
Write-Host " - 암호화 키   : $(if ($env:MASTER_CRYPTO_KEY -or $env:RENT3_CRYPTO_KEY) { '설정됨' } else { '미설정 (필요 시 data\private\coolify.env 작성)' })" -ForegroundColor White
Write-Host "============================================================" -ForegroundColor Cyan

Invoke-Py @("run.py", "--port", "$Port")
exit $LASTEXITCODE
