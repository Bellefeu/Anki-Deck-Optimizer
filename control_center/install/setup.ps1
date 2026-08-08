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
# Under pwsh 7.3 and later this would turn every non-zero exit code from a
# native command into a terminating error, and winget's exit codes are not
# failures in the sense that means. This script decides for itself which ones
# matter, a few lines further down.
$PSNativeCommandUseErrorActionPreference = $false
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location -Path $ProjectRoot

function Bold($m) { Write-Host $m -ForegroundColor White }
function Ok($m)   { Write-Host "  ok    $m" -ForegroundColor Green }
function Miss($m) { Write-Host "  miss  $m" -ForegroundColor Yellow }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red }
function Cmd($m)  { Write-Host "  $ $m" -ForegroundColor Cyan }
function Note($m) { Write-Host "  $m" -ForegroundColor DarkGray }

function Confirm-Step([string]$msg) {
    if ($Yes -or $DryRun) { return $true }
    $r = Read-Host "`n$msg [Y/n]"
    return ($r -eq '' -or $r -match '^(y|yes)$')
}

function Update-EnvironmentPath {
    <# Take up the PATH that installers have been writing to while this ran.

    A process is handed its environment once, at the moment it starts, so
    everything winget added a minute ago is invisible to the very checks below
    that are about to look for it. The registry holds the real thing. #>
    $parts = @([Environment]::GetEnvironmentVariable('Path', 'Machine'),
               [Environment]::GetEnvironmentVariable('Path', 'User')) |
             Where-Object { $_ }
    if ($parts) { $env:PATH = $parts -join ';' }
}

function Find-Python {
    <# The path of a usable interpreter, or $null.

    Delegated so that this script and the PRISM launcher cannot drift apart on
    the question of what counts as an installed Python; see find-python.ps1
    for why the answer is more than a PATH lookup. #>
    $finder = Join-Path $PSScriptRoot 'find-python.ps1'
    if (-not (Test-Path -LiteralPath $finder)) { return $null }
    $found = & $finder
    if ($LASTEXITCODE -ne 0) { return $null }
    $found = (@($found) -join '').Trim()
    if ($found) { return $found }
    return $null
}

function Install-Package([string]$id) {
    <# Ask winget for one package, and do not read its exit code as a verdict.

    "Found an existing package already installed ... No available upgrade
    found" is reported as a failure, and it is the opposite of one: the thing
    this script wanted is on the machine. Rather than keep a table of which
    winget codes are benign, the check that matters happens afterwards, by
    looking for the tool itself. #>
    $argv = @('install', '--id', $id, '-e', '--silent',
              '--accept-package-agreements', '--accept-source-agreements')
    Cmd "winget $($argv -join ' ')"
    if ($DryRun) { return }
    & winget @argv
    if ($LASTEXITCODE -ne 0) {
        Note "winget exited $LASTEXITCODE. Whether that mattered is checked below."
    }
}

$Tools = @('pdftotext', 'pdftoppm', 'pdfinfo', 'pdfimages', 'tesseract', 'node')
$Provides = @{
    'pdftotext' = 'oschwartz10612.Poppler'; 'pdftoppm' = 'oschwartz10612.Poppler'
    'pdfinfo'   = 'oschwartz10612.Poppler'; 'pdfimages' = 'oschwartz10612.Poppler'
    'tesseract' = 'UB-Mannheim.TesseractOCR'; 'node' = 'OpenJS.NodeJS.LTS'
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
if ($py) { Ok $py } else { Miss "not found"; $pkgs += 'Python.Python.3.12' }

# ---- system tools
Bold "2. System tools"
foreach ($t in $Tools) {
    if (Get-Command $t -ErrorAction SilentlyContinue) { Ok $t }
    else { Miss "$t  (from $($Provides[$t]))"; $pkgs += $Provides[$t] }
}
$pkgs = $pkgs | Select-Object -Unique

# ---- install
if ($pkgs.Count -eq 0) {
    Write-Host ""; Ok "everything is already installed"
} else {
    Write-Host ""
    Bold "3. Installing what is missing"
    if (-not (Confirm-Step "Install with winget: $($pkgs -join ', ')")) { Bad "declined"; exit 1 }
    # Every package is attempted. One of them being already present, or even
    # genuinely failing, is not a reason to leave the rest uninstalled.
    foreach ($p in $pkgs) { Install-Package $p }
    Update-EnvironmentPath
}

# ---- what the machine has now. Worth doing even when nothing was installed,
#      because the answer above came from a PATH that may since have grown.
Write-Host ""
Bold "4. Where that leaves things"
if (-not $py) { $py = Find-Python }
if ($py) { Ok $py } else { Bad "Python 3.10+" }
$missing = @()
foreach ($t in $Tools) {
    if (Get-Command $t -ErrorAction SilentlyContinue) { Ok $t }
    else { Miss $t; $missing += $t }
}
if ($missing -and -not $DryRun) {
    Write-Host ""
    Write-Host "  Still missing: $($missing -join ', ')" -ForegroundColor Yellow
    Write-Host "  If winget installed these just now, CLOSE this window and open" -ForegroundColor Yellow
    Write-Host "  PRISM again, so the new window is given the wider PATH." -ForegroundColor Yellow
}

# ---- hand off
Write-Host ""
Bold "5. Handing over to bootstrap.py"
if ($DryRun) {
    Cmd "$(if ($py) { $py } else { 'python3' }) scripts\bootstrap.py"
    Write-Host ""; Write-Host "  (dry run - stopping here)"
    exit 0
}
if (-not $py) {
    Bad "No Python 3.10+ on this computer that setup can find."
    Write-Host ""
    Write-Host "  Everywhere it looked, and what it found there:"
    # Writes its reasons straight to stderr, so they arrive in this window
    # while stdout, which would be the answer, stays empty.
    & (Join-Path $PSScriptRoot 'find-python.ps1') -Explain | Out-Null
    Write-Host ""
    Write-Host "     Install it from https://www.python.org/downloads/windows/ and tick"
    Write-Host "     'Add python.exe to PATH', then open PRISM again."
    exit 1
}
Write-Host ""
& $py 'scripts\bootstrap.py'
exit $LASTEXITCODE
