# Build portable Windows onedir bundle for the SBF -> TIFF geotagger GUI.
#
# IMPORTANT: PyInstaller MUST write its EXE to a real local NTFS disk. Writing
# to a virtual / streamed filesystem (Google Drive for Desktop "G:\My Drive\..",
# OneDrive on-demand, Dropbox Smart Sync, etc.) silently corrupts the embedded
# PKG archive bytes inside the .exe.
#
# Usage (PowerShell):
#   cd ...\TileBasedFocus\tools
#   .\build_gps_correlator_windows.ps1                     # default Local-AppData workpath
#   .\build_gps_correlator_windows.ps1 -DistRoot "D:\Apps" # alternate dist path
#
# Output: <DistRoot>\GPS Correlator\GPS Correlator.exe
[CmdletBinding()]
param(
    [string]$BuildRoot = "$env:LOCALAPPDATA\GPSCorrelatorBuild",
    [string]$DistRoot  = "C:\Parallax Apps"
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Test-VirtualFsPath {
    param([string]$Path)
    $full = [IO.Path]::GetFullPath($Path)
    return ($full -imatch '\\(My Drive|OneDrive[^\\]*|Dropbox)(\\|$)')
}
foreach ($p in @($BuildRoot, $DistRoot)) {
    if (Test-VirtualFsPath -Path $p) {
        throw "Path '$p' is on a virtual/streamed filesystem (Google Drive / OneDrive / Dropbox). PyInstaller PKG embedding will corrupt the EXE there. Choose a real local NTFS path."
    }
}
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
New-Item -ItemType Directory -Force -Path $DistRoot | Out-Null

$work = Join-Path $BuildRoot 'build'

# Locate a usable Python executable (must be a single path for ``& $python -m``).
$python = $null
$candidates = @(
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
)
foreach ($c in $candidates) {
    if (Test-Path -LiteralPath $c) { $python = $c; break }
}
if ($null -eq $python -and (Get-Command py -ErrorAction SilentlyContinue)) {
    $resolved = & py -3 -c "import sys; print(sys.executable)" 2>$null
    if ($resolved -and (Test-Path -LiteralPath $resolved.Trim())) {
        $python = $resolved.Trim()
    }
}
if ($null -eq $python) {
    throw "No Python interpreter found. Install Python 3.11+ or the 'py' launcher."
}

Write-Host "Python:     $python"
Write-Host "Workpath:   $work"
Write-Host "Distpath:   $DistRoot"
Write-Host ""

Write-Host "Ensuring build dependencies..."
& $python -m pip install --upgrade pyinstaller PySide6 matplotlib tifffile numpy

Write-Host "Running PyInstaller (--clean) ..."
& $python -m PyInstaller --noconfirm --clean `
    --workpath $work `
    --distpath $DistRoot `
    GPSCorrelator.spec

$exe = Join-Path $DistRoot 'GPS Correlator\GPS Correlator.exe'
if (-not (Test-Path -LiteralPath $exe)) {
    throw "Build finished but EXE not found at $exe"
}

Write-Host ""
Write-Host "Done. EXE: $exe"
Write-Host ""
Write-Host "Notes:"
Write-Host "  * ExifTool is NOT bundled. The GUI auto-detects exiftool.exe on PATH"
Write-Host "    or at C:\ExifTool\exiftool-13.58_64\exiftool.exe. Install it on every"
Write-Host "    target machine for fast in-place EXIF writes."
Write-Host "  * To copy the bundle to another machine, move the whole 'GPS Correlator'"
Write-Host "    folder (exe + _internal\) to a local NTFS path, NOT a virtual drive."
