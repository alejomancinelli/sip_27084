<#
.SYNOPSIS
    Verifies / prepares Basler GigE camera support on Windows.

.DESCRIPTION
    - Capture needs only the `pypylon` wheel, which bundles the pylon runtime.
      It is already installed by requirements.txt.
    - The full "pylon Camera Software Suite" installer is OPTIONAL. It only adds
      the pylon Viewer, the IP Configurator, and the GigE Vision Filter Driver
      (which improves streaming throughput).

    This script therefore only diagnoses and reports; it installs nothing silently.

    The Linux counterpart is setup\cameras\linux\basler.sh, which does install the
    SDK because pylon is not bundled the same way there.

    NOTE: keep this file pure ASCII. Windows PowerShell 5.1 decodes .ps1 files as
    ANSI (cp1252) unless they carry a UTF-8 BOM, so non-ASCII characters corrupt
    the parse.

.PARAMETER Ip
    Optional camera IP to probe for reachability.

.EXAMPLE
    .\setup\cameras\windows\basler.ps1
    .\setup\cameras\windows\basler.ps1 -Ip 192.168.1.101
#>
[CmdletBinding()]
param(
    [string]$Ip
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "_common.ps1")

Write-Banner "Basler GigE - Windows check"

if (-not (Test-Venv)) { exit 1 }

# --- 1. pypylon (the only hard requirement for capture) ---------------------
$Version = Get-ModuleVersion "pypylon.pylon"
if ($Version) {
    Write-Ok "pypylon importable (bundled pylon runtime) - $Version"
} else {
    Write-Bad "pypylon not importable: $ImportError"
    Write-Hint ".venv\Scripts\python.exe -m pip install pypylon"
    exit 1
}

# --- 2. Optional pylon suite (Viewer / IP Configurator / filter driver) ----
$PylonRoots = @(
    "C:\Program Files\Basler\pylon",
    "C:\Program Files (x86)\Basler\pylon"
) | Where-Object { Test-Path $_ }

if ($PylonRoots) {
    Write-Ok "pylon Camera Software Suite installed ($($PylonRoots -join ', '))"

    $Viewer = Get-ChildItem -Path $PylonRoots -Recurse -Filter "pylonviewer.exe" `
                -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($Viewer) {
        Write-Ok "pylon Viewer: $($Viewer.FullName)"
        Write-Hint "Use it to confirm the camera streams before blaming the app."
    }

    $IpConfigurator = Get-ChildItem -Path $PylonRoots -Recurse -Filter "IpConfigurator.exe" `
                        -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($IpConfigurator) { Write-Ok "pylon IP Configurator: $($IpConfigurator.FullName)" }
} else {
    Write-Note "pylon suite not installed (optional)."
    Write-Hint "Capture still works via pypylon. Install it only if you want"
    Write-Hint "the Viewer, the IP Configurator, or the GigE Filter Driver:"
    Write-Hint "https://www.baslerweb.com/en/software/pylon/"
}

if ($env:GENICAM_GENTL64_PATH) {
    Write-Ok "GENICAM_GENTL64_PATH is set"
} else {
    Write-Note "GENICAM_GENTL64_PATH not set (pypylon does not need it)"
}

# --- 3. Enumerate cameras --------------------------------------------------
Show-DetectedCameras

# --- 4. Network reachability ----------------------------------------------
if ($Ip) { Test-CameraPing $Ip }

Write-Host ""
Write-Banner "Done. Set cameras.camera_1.driver: basler_gige in config.yaml to use it."
exit 0
