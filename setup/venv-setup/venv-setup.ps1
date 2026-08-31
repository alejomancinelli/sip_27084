<#
.SYNOPSIS
    Creates and provisions the project virtual environment on Windows.

.DESCRIPTION
    Locates a 64-bit CPython 3.10, creates .venv, installs requirements.txt, adds the
    Sentech stapipy wheel if one was copied into packages\, and finishes by running
    verify_env.py.

    Two things it deliberately does NOT do:

      - No --system-site-packages. On Linux that flag exists to expose apt-installed
        native bindings (python3-gi, python3-libgpiod); on Windows nothing useful
        comes from the OS.
      - It never touches Activate.ps1. That file ends with an Authenticode signature
        block, and any code appended after it makes PowerShell refuse to parse the
        whole script. Environment variables a framework needs before it is imported
        belong to the module that imports it, so they apply however the app is
        launched.

    NOTE: keep this file pure ASCII. Windows PowerShell 5.1 decodes .ps1 files as
    ANSI (cp1252) unless they carry a UTF-8 BOM, so non-ASCII characters corrupt the
    parse.

.PARAMETER Force
    Delete any existing .venv and recreate it from scratch.

.PARAMETER SkipInstall
    Only create the venv; do not install anything. Useful when debugging the venv.

.EXAMPLE
    .\setup\venv-setup\venv-setup.ps1
    .\setup\venv-setup\venv-setup.ps1 -Force
#>
[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

# Paths resolve from the script location, not the CWD, so the script works when
# invoked from any directory.
$ProjectRoot  = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$VenvPath     = Join-Path $ProjectRoot ".venv"
$VenvPython   = Join-Path $VenvPath "Scripts\python.exe"
$Requirements = Join-Path $ProjectRoot "requirements.txt"
$PackagesDir  = Join-Path $ProjectRoot "packages"
$VerifyScript = Join-Path $PSScriptRoot "verify_env.py"

function Write-Step { param([string]$Message) Write-Host "`n[STEP] $Message" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Message) Write-Host "  OK   $Message"   -ForegroundColor Green }
function Write-Note { param([string]$Message) Write-Host " NOTE  $Message"   -ForegroundColor Yellow }
function Write-Hint { param([string]$Message) Write-Host "        $Message"  -ForegroundColor DarkGray }

Write-Host "======================================================================"
Write-Host " Project environment provisioning (Windows)"
Write-Host "======================================================================"
Write-Host " Project: $ProjectRoot"

# --- 1/5  Locate CPython 3.10 x64 -------------------------------------------
# Pinned to 3.10 for two reasons that both come down to prebuilt wheels: stapipy
# ships cp310 only, and stardist has a cp310-win_amd64 build. Without MSVC Build
# Tools on this machine, anything that has to compile from source cannot install.
Write-Step "1/5  Locating 64-bit CPython 3.10"

$PyExe = $null
try {
    $Probe = & py -3.10-64 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0) { $PyExe = $Probe.Trim() }
} catch { }

if (-not $PyExe) {
    $Fallback = Join-Path $env:LOCALAPPDATA "Programs\Python\Python310\python.exe"
    if (Test-Path $Fallback) { $PyExe = $Fallback }
}

if (-not $PyExe) {
    throw ("64-bit CPython 3.10 not found. Install it from " +
           "https://www.python.org/downloads/release/python-31011/ " +
           "or run 'winget install Python.Python.3.10', then retry.")
}

$PyVersion = (& $PyExe -c "import platform; print(platform.python_version())").Trim()
$PyBits    = (& $PyExe -c "import platform; print(platform.architecture()[0])").Trim()
if ($PyBits -ne "64bit") {
    throw "The interpreter found is $PyBits. 64bit is required: $PyExe"
}
Write-Ok "Python $PyVersion $PyBits - $PyExe"

# --- 2/5  Create the venv ----------------------------------------------------
Write-Step "2/5  Creating the virtual environment"

if (Test-Path $VenvPath) {
    if ($Force) {
        Write-Note "Removing the existing .venv (-Force)"
        Remove-Item -Recurse -Force $VenvPath
    } else {
        Write-Note ".venv already exists - reusing it. Use -Force to recreate."
    }
}

if (-not (Test-Path $VenvPath)) {
    & $PyExe -m venv $VenvPath
    if (-not $?) { throw "venv creation failed." }
    Write-Ok "venv created at $VenvPath"
}

if (-not (Test-Path $VenvPython)) {
    throw "$VenvPython is missing - the venv is corrupt. Retry with -Force."
}

if ($SkipInstall) {
    Write-Note "-SkipInstall given: stopping after venv creation."
    exit 0
}

# --- 3/5  PyPI dependencies --------------------------------------------------
Write-Step "3/5  Installing dependencies (large download: TensorFlow is ~250 MB)"

& $VenvPython -m pip install --upgrade pip setuptools wheel
if (-not $?) { throw "Failed to upgrade pip." }

& $VenvPython -m pip install -r $Requirements
if (-not $?) { throw "'pip install -r requirements.txt' failed." }
Write-Ok "requirements.txt installed"

# --- 4/5  stapipy (Sentech) from the local wheel -----------------------------
# Not published on PyPI: it ships inside the Windows SentechSDK. Optional - without
# it the st_gige driver cannot connect, but the app still starts.
Write-Step "4/5  Installing stapipy (Sentech) if a wheel exists in packages\"

$StapiWheel = $null
if (Test-Path $PackagesDir) {
    $StapiWheel = Get-ChildItem -Path $PackagesDir -Filter "stapipy*win_amd64.whl" `
                    -ErrorAction SilentlyContinue | Select-Object -First 1
}

if ($StapiWheel) {
    & $VenvPython -m pip install $StapiWheel.FullName
    if ($?) {
        Write-Ok "stapipy installed from $($StapiWheel.Name)"
    } else {
        Write-Note "Install of $($StapiWheel.Name) failed - st_gige unavailable."
    }
} else {
    Write-Note "No stapipy*win_amd64.whl in packages\ - st_gige driver unavailable."
    Write-Hint "To enable Sentech cameras: install the Windows SentechSDK with Python"
    Write-Hint "support, copy its wheel into packages\, and re-run this script (or"
    Write-Hint "setup\cameras\windows\sentech.ps1)."
}

# --- 5/5  Verification -------------------------------------------------------
Write-Step "5/5  Verifying the environment"

& $VenvPython $VerifyScript
$VerifyExit = $LASTEXITCODE

Write-Host "`n======================================================================"
if ($VerifyExit -eq 0) {
    Write-Host " Environment ready." -ForegroundColor Green
    Write-Host "======================================================================"
    Write-Host ""
    Write-Host " Run:    .\.venv\Scripts\python.exe main.py             (with window)"
    Write-Host "         .\.venv\Scripts\python.exe main.py --headless  (no window)"
    Write-Host " Tests:  .\.venv\Scripts\python.exe -m pytest -q"
} else {
    Write-Host " Environment INCOMPLETE - review the failures above." -ForegroundColor Red
    Write-Host "======================================================================"
}

exit $VerifyExit
