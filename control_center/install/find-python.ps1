<#
    Print the path of a Python that PRISM can run on, or print nothing.

        .\control_center\install\find-python.ps1
        .\control_center\install\find-python.ps1 -Explain

    Exits 0 having written one line to stdout when it finds one, and exits 1
    having written nothing when it does not. Nothing else is written to stdout,
    so a caller can read the answer straight out of the pipe. -Explain writes
    every candidate it considered, and why each one was turned down, to stderr,
    which leaves the answer on stdout untouched.

    This file exists because "Python is installed" and "Python is on PATH" are
    different facts on Windows, and only the second one is what Get-Command can
    see. The installer leaves the Add to PATH box unticked by default, winget's
    silent install does not tick it either, and a per user install lands in
    %LOCALAPPDATA%\Programs\Python where nothing on PATH points. A machine in
    that state reports no Python to every check that asks the shell, while
    winget correctly refuses to install a second copy, and setup is stuck
    between the two answers. So this asks the places Windows actually keeps
    interpreters, in the order a person would want them used.

    What it prints is always a plain path to a real python.exe, never a
    launcher and an argument, because both callers go on to quote it.
#>

[CmdletBinding()]
param([switch]$Explain)

$MinimumMinor = 10

# The interpreter reports its own version and its own path, and writes nothing
# else, which is what makes the reply safe to match against. sys.executable is
# what turns "py -3" into the interpreter py chose.
#
# It is written without a single quote character in it, which is why the
# separators are spelled chr(46) and chr(10) rather than "." and "\n". Windows
# PowerShell hands an argument to a native program without escaping the double
# quotes inside it, so a probe written the readable way arrives at Python as
#
#     import sys; sys.stdout.write(%d.%d\n%s % (...))
#
# which is a SyntaxError. The interpreter then exits non-zero and is written
# off as unusable, and since that happens to every interpreter on the machine,
# a perfectly well equipped computer reports that it has no Python at all.
$Probe = 'import sys; sys.stdout.write(str(sys.version_info[0])+chr(46)+str(sys.version_info[1])+chr(10)+sys.executable)'

# The Microsoft Store aliases. These are zero byte stubs that open the Store
# rather than run anything, so they would answer the probe with a failure and
# be discarded anyway; skipping them by path just saves the launch.
$StoreAliases = '\Microsoft\WindowsApps\'

# Straight to stderr rather than through Write-Error or Write-Host, so that it
# cannot land on stdout and be mistaken for the answer.
function Say([string]$message) {
    if ($Explain) { [Console]::Error.WriteLine($message) }
}

$Candidates = [System.Collections.Generic.List[object]]::new()
$Seen = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase)

function Add-Candidate([string]$exe, [string[]]$argv, [string]$where) {
    if (-not $exe) { return }
    if ($exe -like "*$StoreAliases*") { Say "skip  $exe (Microsoft Store stub)"; return }
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { return }
    $key = "$exe $($argv -join ' ')"
    if (-not $Seen.Add($key)) { return }
    $Candidates.Add([pscustomobject]@{ Exe = $exe; Argv = @($argv); Where = $where })
}

# ---- what the shell can already see. First, because an interpreter the rest
#      of the machine agrees on is the one whose installed packages and PATH
#      the person has been using.
foreach ($name in @('py', 'python3.13', 'python3.12', 'python3.11',
                    'python3.10', 'python3', 'python')) {
    $found = Get-Command $name -All -CommandType Application -ErrorAction SilentlyContinue
    foreach ($command in @($found)) {
        if (-not $command) { continue }
        # The launcher is not itself an interpreter, and -3 is what stops it
        # choosing a Python 2 that happens to be registered alongside.
        if ($command.Name -eq 'py.exe') { Add-Candidate $command.Source @('-3') 'PATH' }
        else { Add-Candidate $command.Source @() 'PATH' }
    }
}

# ---- the registry, which is where an installer records itself whether or not
#      it touched PATH (PEP 514). Per user first: a machine with both is
#      usually one where the per user install is the newer, deliberate one.
foreach ($hive in @('HKCU:\SOFTWARE\Python\PythonCore',
                    'HKLM:\SOFTWARE\Python\PythonCore',
                    'HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore')) {
    if (-not (Test-Path $hive)) { continue }
    $versions = Get-ChildItem $hive -ErrorAction SilentlyContinue | Sort-Object {
        try { [version](($_.PSChildName -split '-')[0]) } catch { [version]'0.0' }
    } -Descending
    foreach ($version in $versions) {
        $install = Get-ItemProperty -Path (Join-Path $version.PSPath 'InstallPath') `
                                    -ErrorAction SilentlyContinue
        if (-not $install) { continue }
        if ($install.ExecutablePath) {
            Add-Candidate $install.ExecutablePath @() 'registry'
        } elseif ($install.'(default)') {
            Add-Candidate (Join-Path $install.'(default)' 'python.exe') @() 'registry'
        }
    }
}

# ---- and the folders themselves, for an install that recorded nothing. These
#      are the same locations the dashboard searches at run time.
$roots = @((Join-Path $env:LOCALAPPDATA 'Programs\Python'),
           $env:ProgramFiles, ${env:ProgramFiles(x86)}, 'C:\')
foreach ($root in $roots) {
    if (-not $root -or -not (Test-Path -LiteralPath $root)) { continue }
    $directories = Get-ChildItem -LiteralPath $root -Directory -Filter 'Python3*' `
                                 -ErrorAction SilentlyContinue | Sort-Object Name -Descending
    foreach ($directory in $directories) {
        Add-Candidate (Join-Path $directory.FullName 'python.exe') @() 'install directory'
    }
}

# The launcher ships with the installer and lands beside Windows itself, which
# is a directory on PATH often enough to be worth naming outright when it is not.
Add-Candidate (Join-Path $env:SystemRoot 'py.exe') @('-3') 'Windows directory'

# ---- ask each one what it is. An interpreter that cannot answer, or answers
#      with a version below the floor, is not a candidate whatever its path
#      suggests: a 3.9 left behind by an older install looks identical from
#      the outside.
Say "$($Candidates.Count) candidate(s) to try."
foreach ($candidate in $Candidates) {
    $shown = "$($candidate.Exe) $($candidate.Argv -join ' ')".Trim() + "  [$($candidate.Where)]"
    $argv = @($candidate.Argv) + @('-c', $Probe)
    $output = @()
    try {
        $output = @(& $candidate.Exe @argv 2>&1)
    } catch {
        Say "no    $shown : $($_.Exception.Message)"
        continue
    }
    $text = (($output | ForEach-Object { "$_" }) -join "`n").Trim()
    if ($LASTEXITCODE -ne 0) {
        Say "no    $shown : exit $LASTEXITCODE $text"
        continue
    }
    $lines = $text -split "`r?`n"
    $reported = $lines[0].Trim()
    # Split rather than a capture group: $Matches is an automatic variable set
    # by whichever comparison ran last, and reading it after a -notmatch that
    # fell through is a promise PowerShell does not make.
    if ($reported -notmatch '^[0-9]+\.[0-9]+$') {
        Say "no    $shown : said '$reported', which is not a version"
        continue
    }
    $parts = $reported.Split('.')
    if ([int]$parts[0] -ne 3 -or [int]$parts[1] -lt $MinimumMinor) {
        Say "no    $shown : Python $reported is below 3.$MinimumMinor"
        continue
    }
    # Report where the interpreter says it lives rather than how it was
    # reached, so that "py -3" comes back as a path a caller can just quote.
    $executable = if ($lines.Count -ge 2) { $lines[1].Trim() } else { '' }
    if (-not $executable) {
        if ($candidate.Argv.Count) {
            Say "no    $shown : Python $reported would not say where it lives"
            continue
        }
        $executable = $candidate.Exe
    }
    Say "yes   $shown : Python $reported at $executable"
    Write-Output $executable
    exit 0
}

Say "No Python 3.$MinimumMinor or newer was found."
exit 1
