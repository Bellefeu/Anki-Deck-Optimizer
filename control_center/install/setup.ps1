<#
    One-command setup for Windows.

        .\control_center\install\setup.ps1              install and check
        .\control_center\install\setup.ps1 -DryRun      show changes only
        .\control_center\install\setup.ps1 -Yes         do not ask before installing

    Installs Python 3.12, poppler, tesseract and Node.js via winget - but only the
    ones you are actually missing. Safe to re-run.

    It does NOT install Anki. You have that already.

    If PowerShell refuses to run this ("running scripts is disabled"), open
    PowerShell and run once:

        Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

    winget ships with Windows 11 and recent Windows 10. If you do not have it,
    install "App Installer" from the Microsoft Store, or use WSL and the bash installer
    instead - WSL is honestly the smoother path for this pipeline.
#>

[CmdletBinding()]
param([switch]$DryRun, [switch]$Yes)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location -Path $ProjectRoot

function Bold($m) { Write-Host $m -ForegroundColor White }
function Ok($m)   { Write-Host "  ok    $m" -ForegroundColor Green }
function Miss($m) { Write-Host "  miss  $m" -ForegroundColor Yellow }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red }
function Cmd($m)  { Write-Host "  $ $m" -ForegroundColor Cyan }

function Invoke-Step([string]$exe, [string[]]$argv) {
    Cmd "$exe $($argv -join ' ')"
    if ($DryRun) { return }
    & $exe @argv
    if ($LASTEXITCODE -ne 0) { throw "command failed: $exe $($argv -join ' ')" }
}

function Confirm-Step([string]$msg) {
    if ($Yes -or $DryRun) { return $true }
    $r = Read-Host "`n$msg [Y/n]"
    return ($r -eq '' -or $r -match '^(y|yes)$')
}

function Find-Python {
    foreach ($c in @('python3.13','python3.12','python3.11','python3.10','python3','python','py')) {
        $exe = Get-Command $c -ErrorAction SilentlyContinue
        if (-not $exe) { continue }
        try {
            $v = & $c -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>$null
            if ($v -match '^3\.(\d+)$' -and [int]$Matches[1] -ge 10) { return $c }
        } catch { }
    }
    return $null
}

Bold "=== ANKI DECK OPTIMIZATION - SETUP ==="
Write-Host "  os: Windows    package manager: winget"
if ($DryRun) { Write-Host "  DRY RUN - nothing will be installed" }
Write-Host ""

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Bad "winget not found."
    Write-Host "     Install 'App Installer' from the Microsoft Store, then re-run."
    Write-Host "     Or install WSL (wsl --install), then open Prism again."
    exit 1
}

# ---- Python. This script exists because bootstrap.py cannot install the Python
#      it needs in order to run.
Bold "1. Python 3.10+"
$py = Find-Python
$pkgs = @()
if ($py) { Ok "$py" } else { Miss "not found"; $pkgs += 'Python.Python.3.12' }

# ---- system tools
Bold "2. System tools"
$provides = @{
    'pdftotext' = 'oschwartz10612.Poppler'; 'pdftoppm' = 'oschwartz10612.Poppler'
    'pdfinfo'   = 'oschwartz10612.Poppler'; 'pdfimages' = 'oschwartz10612.Poppler'
    'tesseract' = 'UB-Mannheim.TesseractOCR'; 'node' = 'OpenJS.NodeJS.LTS'
}
foreach ($t in @('pdftotext','pdftoppm','pdfinfo','pdfimages','tesseract','node')) {
    if (Get-Command $t -ErrorAction SilentlyContinue) { Ok $t }
    else { Miss "$t  (from $($provides[$t]))"; $pkgs += $provides[$t] }
}
$pkgs = $pkgs | Select-Object -Unique

# ---- install
if ($pkgs.Count -eq 0) {
    Write-Host ""; Ok "everything is already installed"
} else {
    Write-Host ""
    Bold "3. Installing what is missing"
    if (-not (Confirm-Step "Install with winget: $($pkgs -join ', ')")) { Bad "declined"; exit 1 }
    foreach ($p in $pkgs) {
        Invoke-Step 'winget' @('install','--id',$p,'-e','--silent',
                               '--accept-package-agreements','--accept-source-agreements')
    }
    Write-Host ""
    Write-Host "  Note: winget adds these to PATH, but an already-open terminal will not" -ForegroundColor Yellow
    Write-Host "  see them. If the next step says something is still missing, CLOSE this" -ForegroundColor Yellow
    Write-Host "  window. Close it, then open Prism again." -ForegroundColor Yellow
    if (-not $py) { $py = Find-Python }
}

# ---- hand off
Write-Host ""
Bold "4. Handing over to bootstrap.py"
if ($DryRun) {
    Cmd "$(if ($py) { $py } else { 'python3' }) scripts\bootstrap.py"
    Write-Host ""; Write-Host "  (dry run - stopping here)"
    exit 0
}
if (-not $py) {
    Bad "Python 3.10+ still not on PATH."
    Write-Host "     Close this window, then open Prism again."
    exit 1
}
Write-Host ""
& $py 'scripts\bootstrap.py'
exit $LASTEXITCODE
