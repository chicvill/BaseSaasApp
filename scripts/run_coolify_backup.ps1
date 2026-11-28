<#
.SYNOPSIS
  Restore a Coolify Rent3 backup (.tar.gz) into data\private\coolify_live and start the local server.

.DESCRIPTION
  - Keeps data\rent3_pool untouched; uses git-ignored data\private\coolify_live.
  - Verifies the backup and restores into a staging folder first, replacing the previous folder only on success.
  - Automatically loads optional local secrets (e.g. MASTER_CRYPTO_KEY) from data\private\coolify.env if present.

.EXAMPLE
  .\scripts\run_coolify_backup.ps1
  .\scripts\run_coolify_backup.ps1 "C:\Users\USER\Downloads\rent3_pool_20261127_133644.tar.gz"
  .\scripts\run_coolify_backup.ps1 -Latest
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

function Get-PythonArgs {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.14 --version *> $null
        if ($LASTEXITCODE -eq 0) { return @("py", "-3.14") }
        return @("py")
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @("python")
    }
    throw "Python launcher (py or python) not found."
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

# Optional: load local-only secrets (e.g. MASTER_CRYPTO_KEY) from data\private\coolify.env
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
        Write-Host ("[ENV] Loaded from data\private\coolify.env: " + ($loadedKeys -join ", ")) -ForegroundColor Cyan
    }
}

# -Latest: pick newest rent3_pool_*.tar.gz from Downloads
if ($Latest -and -not $BackupFile) {
    $downloads = Join-Path $env:USERPROFILE "Downloads"
    $candidates = @()
    if (Test-Path -LiteralPath $downloads) {
        $candidates += Get-ChildItem -LiteralPath $downloads -Filter "rent3_pool_*.tar.gz" -File -ErrorAction SilentlyContinue
    }
    if ($candidates.Count -eq 0) {
        throw "No rent3_pool_*.tar.gz backup found in $downloads"
    }
    $picked = $candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $BackupFile = $picked.FullName
    Write-Host "[AUTO] Latest backup selected: $BackupFile" -ForegroundColor Cyan
}

# Check port BEFORE touching any files so a running server cannot re-create empty JSON files during swap!
$portBusy = $false
try {
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($conns) { $portBusy = $true }
} catch {
    $portBusy = $false
}
if ($portBusy) {
    Write-Host "[ERROR] Port $Port is already in use! Stop the running server (Ctrl+C) BEFORE restoring or starting." -ForegroundColor Red
    Write-Host "        (실행 중인 서버를 끄지 않고 복구하면 브라우저 요청이 빈 폴더를 덮어쓸 수 있어 중단했습니다.)" -ForegroundColor Yellow
    exit 1
}

# If backup file provided: verify -> restore to staging -> safe swap with rollback
if ($BackupFile) {
    $BackupPath = [System.IO.Path]::GetFullPath($BackupFile)
    if (-not (Test-Path -LiteralPath $BackupPath -PathType Leaf)) {
        throw "Backup file not found: $BackupPath"
    }

    Write-Host "[1/3] Verifying backup: $BackupPath" -ForegroundColor Yellow
    Invoke-Py @("scripts\rent3_backup.py", "--verify", $BackupPath)
    if ($LASTEXITCODE -ne 0) {
        throw "Backup verification failed. Existing folder was left untouched."
    }

    $parentDir = Split-Path -Parent $TargetDir
    if (-not (Test-Path -LiteralPath $parentDir)) {
        New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
    }
    $ts = Get-Date -Format "yyyyMMdd_HHmmss"
    $stagingDir = "${TargetDir}_staging_${ts}"
    $prevDir = "${TargetDir}_prev_${ts}"
    $movedPrev = $false
    try {
        Write-Host "[2/3] Restoring into staging dir: $stagingDir" -ForegroundColor Yellow
        Invoke-Py @("scripts\rent3_backup.py", "--restore", $BackupPath, "--to", $stagingDir)
        if ($LASTEXITCODE -ne 0) {
            throw "Backup restore failed."
        }

        if (Test-Path -LiteralPath $TargetDir) {
            Write-Host "[3/3] Swapping folder safely (preserving rollback copy)..." -ForegroundColor Yellow
            Move-Item -LiteralPath $TargetDir -Destination $prevDir
            $movedPrev = $true
        }
        Move-Item -LiteralPath $stagingDir -Destination $TargetDir
        if ($movedPrev -and (Test-Path -LiteralPath $prevDir)) {
            Remove-Item -LiteralPath $prevDir -Recurse -Force -ErrorAction SilentlyContinue
        }
        Write-Host "[OK] Restored successfully to: $TargetDir" -ForegroundColor Green
    } catch {
        if ($movedPrev -and -not (Test-Path -LiteralPath $TargetDir) -and (Test-Path -LiteralPath $prevDir)) {
            Write-Host "[ROLLBACK] Restoring previous folder: $prevDir -> $TargetDir" -ForegroundColor Yellow
            Move-Item -LiteralPath $prevDir -Destination $TargetDir -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $stagingDir) {
            Remove-Item -LiteralPath $stagingDir -Recurse -Force -ErrorAction SilentlyContinue
        }
        throw
    }
}

if (-not (Test-Path -LiteralPath $TargetDir -PathType Container)) {
    throw "Restored folder not found: $TargetDir. Pass a backup .tar.gz path or use -Latest."
}

if ($RestoreOnly) {
    Write-Host "[OK] Restore complete (-RestoreOnly). Run server with:" -ForegroundColor Green
    Write-Host "  .\scripts\run_coolify_backup.ps1" -ForegroundColor White
    exit 0
}

$env:RENT3_POOL_DIR = $TargetDir
$env:RENT3_DEMO_SEED = "0"
$cryptoStatus = if ($env:MASTER_CRYPTO_KEY -or $env:RENT3_CRYPTO_KEY) { "SET" } else { "NOT SET (optional: data\private\coolify.env)" }

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Starting BaseSaasApp with Coolify Backup Data Pool" -ForegroundColor Cyan
Write-Host " - Pool Dir   : $TargetDir" -ForegroundColor White
Write-Host " - Portal URL : http://localhost:$Port/rent3/" -ForegroundColor White
Write-Host " - Crypto Key : $cryptoStatus" -ForegroundColor White
Write-Host "============================================================" -ForegroundColor Cyan

Invoke-Py @("run.py", "--port", "$Port")
exit $LASTEXITCODE
