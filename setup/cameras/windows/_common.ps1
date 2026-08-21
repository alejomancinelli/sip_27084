<#
.SYNOPSIS
    Shared paths and reporting helpers for the Windows camera setup scripts.

.DESCRIPTION
    Dot-source it from a sibling script:

        . (Join-Path $PSScriptRoot "_common.ps1")

    Every path the camera scripts need is resolved here, so a move of this folder
    is a one-line fix instead of one per script. Same for the reporting helpers and
    the two steps both scripts end with (enumeration and ping).

    NOTE: keep this file pure ASCII. Windows PowerShell 5.1 decodes .ps1 files as
    ANSI (cp1252) unless they carry a UTF-8 BOM, so non-ASCII characters corrupt
    the parse.
#>

# This file lives in setup\cameras\windows\, so the repo root is three levels up.
$CamerasDir  = Split-Path $PSScriptRoot -Parent
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$PackagesDir = Join-Path $ProjectRoot "packages"
$VenvPython  = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

# Both Python helpers sit one level up, next to the platform folders.
$CheckImport = Join-Path $CamerasDir "check_import.py"
$ListCameras = Join-Path $CamerasDir "list_cameras.py"

# The Python helpers print Spanish. Without these two lines PowerShell decodes
# their output with the console codepage and the accents arrive as mojibake.
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Write-Ok {
    param([string]$Message)
    Write-Host "  OK   $Message" -ForegroundColor Green
}

function Write-Note {
    param([string]$Message)
    Write-Host " NOTE  $Message" -ForegroundColor Yellow
}

function Write-Bad {
    param([string]$Message)
    Write-Host " FAIL  $Message" -ForegroundColor Red
}

function Write-Hint {
    param([string]$Message)
    Write-Host "        $Message" -ForegroundColor DarkGray
}

function Write-Banner {
    param([string]$Title)
    Write-Host "======================================================================"
    Write-Host " $Title"
    Write-Host "======================================================================"
}

function Test-Venv {
    <# True when the project venv exists; reports how to create it when it does not. #>
    if (Test-Path $VenvPython) { return $true }
    Write-Bad "No venv found at $VenvPython"
    Write-Hint "Run setup\venv-setup\venv-setup.ps1 first."
    return $false
}

function Get-ModuleVersion {
    <#
    Returns the version reported by check_import.py, or $null when the module does
    not import. The reason is left in $ImportError for the caller to report.

    check_import.py keeps its traceback on stdout on purpose: PowerShell 5.1 turns
    a native command's redirected stderr into a terminating NativeCommandError
    under $ErrorActionPreference = "Stop", aborting the caller instead of
    reporting the failure.
    #>
    param([string]$ModuleName)
    $Output = & $VenvPython $CheckImport $ModuleName
    if ($LASTEXITCODE -eq 0) {
        $global:ImportError = $null
        return $Output
    }
    $global:ImportError = $Output
    return $null
}

function Show-DetectedCameras {
    <# Runs list_cameras.py. No camera attached is not a setup failure. #>
    Write-Host "`n--- Cameras visible to the installed SDKs ---"
    if (-not (Test-Path $ListCameras)) {
        Write-Note "list_cameras.py not found at $ListCameras; skipping enumeration."
        return
    }
    & $VenvPython $ListCameras
    if ($LASTEXITCODE -ne 0) {
        Write-Note "No camera attached right now. Support is installed correctly."
    }
}

function Test-CameraPing {
    param([string]$Address)
    Write-Host "`n--- Network check for $Address ---"
    if (Test-Connection -ComputerName $Address -Count 2 -Quiet) {
        Write-Ok "$Address responds to ping"
        return
    }
    Write-Note "$Address does not respond to ping."
    Write-Hint "GigE Vision cameras can still stream without ICMP, but check:"
    Write-Hint "- host NIC and camera on the same subnet"
    Write-Hint "- firewall allows GVCP (UDP 3956) inbound"
}
