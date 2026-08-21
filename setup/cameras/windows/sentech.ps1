<#
.SYNOPSIS
    Installs / verifies Sentech (Omron) GigE camera support on Windows.

.DESCRIPTION
    1. Confirm the Windows SentechSDK is installed.
    2. pip install the stapipy wheel that ships INSIDE that SDK.
    3. Enumerate cameras to prove it works.

    stapipy is NOT on PyPI. The wheel must be copied into packages\ from the SDK
    installation (typically under a "Python" folder inside the SentechSDK
    directory). Without it the st_gige driver cannot connect, though the app still
    starts and other drivers keep working.

    The Linux counterpart is setup\cameras\linux\sentech.sh.

    NOTE: keep this file pure ASCII. Windows PowerShell 5.1 decodes .ps1 files as
    ANSI (cp1252) unless they carry a UTF-8 BOM, so non-ASCII characters corrupt
    the parse.

.PARAMETER Ip
    Optional camera IP to probe for reachability.

.EXAMPLE
    .\setup\cameras\windows\sentech.ps1
    .\setup\cameras\windows\sentech.ps1 -Ip 192.168.1.101
#>
[CmdletBinding()]
param(
    [string]$Ip
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "_common.ps1")

# The venv is Python 3.10 64-bit, so the wheel has to be the cp310 win_amd64 build.
$WheelPattern = "stapipy*win_amd64.whl"

Write-Banner "Sentech / Omron GigE - Windows setup"

if (-not (Test-Venv)) { exit 1 }

# --- 1. Vendor SDK ----------------------------------------------------------
$SdkRoots = @(
    "C:\Program Files\OMRON_SENTECH",
    "C:\Program Files (x86)\OMRON_SENTECH"
) | Where-Object { Test-Path $_ }

if ($SdkRoots) {
    Write-Ok "SentechSDK found: $($SdkRoots -join ', ')"
    $Viewer = Get-ChildItem -Path $SdkRoots -Recurse -Filter "StViewer*.exe" `
                -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($Viewer) {
        Write-Ok "StViewer: $($Viewer.FullName)"
        Write-Hint "Use it to confirm the camera streams before blaming the app."
    }
} else {
    Write-Bad "SentechSDK for Windows is not installed."
    Write-Hint "Download and run the installer, then re-run this script:"
    Write-Hint "https://www.sentech.co.jp/en/products/SDK/"
    Write-Hint "Make sure to select Python support so the stapipy wheel is included."
}

if ($env:GENICAM_GENTL64_PATH -and $env:GENICAM_GENTL64_PATH -match "SENTECH") {
    Write-Ok "GENICAM_GENTL64_PATH includes the Sentech GenTL producer"
} else {
    Write-Note "GENICAM_GENTL64_PATH does not mention SENTECH."
    Write-Hint "The installer normally sets it machine-wide. A fresh install"
    Write-Hint "needs a new shell (or a logoff) for it to be visible."
}

# --- 2. stapipy wheel -------------------------------------------------------
$Version = Get-ModuleVersion "stapipy"
if ($Version) {
    Write-Ok "stapipy already installed in the venv - $Version"
} else {
    Write-Note "stapipy not importable yet ($ImportError)"

    $Wheel = $null
    if (Test-Path $PackagesDir) {
        $Wheel = Get-ChildItem -Path $PackagesDir -Filter $WheelPattern `
                   -ErrorAction SilentlyContinue | Select-Object -First 1
    }

    if (-not $Wheel) {
        Write-Bad "No $WheelPattern in $PackagesDir"
        Write-Hint "Locate the wheel inside the SentechSDK installation"
        Write-Hint "(look for a 'Python' folder under the SDK directory),"
        Write-Hint "copy it into packages\, and re-run this script."
        Write-Hint "It must be the cp310 win_amd64 build: the venv is Python 3.10 64-bit."
        exit 1
    }

    Write-Host "`nInstalling $($Wheel.Name) ..."
    & $VenvPython -m pip install $Wheel.FullName
    if ($LASTEXITCODE -ne 0) {
        Write-Bad "pip install failed for $($Wheel.Name)"
        Write-Hint "If pip reports the wheel is 'not a supported wheel on this"
        Write-Hint "platform', it is the wrong build: cp310 + win_amd64 is what"
        Write-Hint "this venv needs."
        exit 1
    }

    # pip exit 0 is not enough: stapipy is a compiled extension and can install
    # cleanly yet fail to load the SDK DLLs.
    $Version = Get-ModuleVersion "stapipy"
    if ($Version) {
        Write-Ok "stapipy installed and importable - $Version"
    } else {
        Write-Bad "stapipy installed but NOT importable: $ImportError"
        Write-Hint "The wheel is in place but its native dependencies did not"
        Write-Hint "load. Usually the SentechSDK runtime is missing or a new"
        Write-Hint "shell is needed so the SDK's PATH entries are visible."
        exit 1
    }
}

# --- 3. Enumerate cameras --------------------------------------------------
Show-DetectedCameras

# --- 4. Network reachability ----------------------------------------------
if ($Ip) { Test-CameraPing $Ip }

Write-Host ""
Write-Banner "Done. Set cameras.camera_1.driver: st_gige in config.yaml to use it."
exit 0
