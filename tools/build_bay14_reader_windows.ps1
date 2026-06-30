# Build portable Windows onedir app for Bay14 reader GUI (PyInstaller).
#
# IMPORTANT: PyInstaller MUST write its EXE to a real local NTFS disk. Writing
# to a virtual / streamed filesystem (Google Drive for Desktop "G:\My Drive\..",
# OneDrive on-demand, Dropbox Smart Sync, etc.) silently corrupts the embedded
# PKG archive bytes inside the .exe. The build "succeeds", but on launch the
# bootloader pops:
#   "Could not load PyInstaller's embedded PKG archive from the executable (...)"
# Fix is structural: always redirect --workpath / --distpath to C:\.
#
# Usage (PowerShell):
#   cd ...\TileBasedFocus\tools
#   .\build_bay14_reader_windows.ps1                              # default: ship to C:\Parallax Apps
#   .\build_bay14_reader_windows.ps1 -DistRoot "D:\Apps"          # alternate ship target
#   .\build_bay14_reader_windows.ps1 -BuildRoot D:\Build\Bay14Reader
#
# Output: <DistRoot>\Bay14Reader\Bay14Reader.exe  (+ _internal\)
[CmdletBinding()]
param(
    [string]$BuildRoot = "$env:LOCALAPPDATA\Bay14ReaderBuild",
    [string]$DistRoot  = "C:\Parallax Apps"
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$hostDir = Join-Path (Split-Path $PSScriptRoot -Parent) 'host'

# Refuse to build straight into a virtual filesystem (Google Drive, OneDrive...).
function Test-VirtualFsPath {
    param([string]$Path)
    $full = [IO.Path]::GetFullPath($Path)
    # Match \My Drive\, \OneDrive\, \Dropbox\ anywhere in the path; case-insensitive.
    return ($full -imatch '\\(My Drive|OneDrive[^\\]*|Dropbox)(\\|$)')
}
foreach ($p in @($BuildRoot, $DistRoot)) {
    if (Test-VirtualFsPath -Path $p) {
        throw "Path '$p' is on a virtual/streamed filesystem (Google Drive / OneDrive / Dropbox). PyInstaller PKG embedding will corrupt the EXE there. Choose a real local NTFS path."
    }
}
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
New-Item -ItemType Directory -Force -Path $DistRoot  | Out-Null
$workPath = Join-Path $BuildRoot 'build'
$distPath = $DistRoot

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

Write-Host "Installing deps from host\requirements*.txt ..."
& $python -m pip install -r (Join-Path $hostDir 'requirements.txt') -r (Join-Path $hostDir 'requirements-build.txt')

$iconScript = Join-Path $hostDir 'tools\make_exe_icon.py'
if (Test-Path -LiteralPath $iconScript) {
    Write-Host "Ensuring Windows icon (.ico) ..."
    & $python $iconScript
}

Write-Host "Running PyInstaller (--clean) ..."
Write-Host "  workpath: $workPath"
Write-Host "  distpath: $distPath"
& $python -m PyInstaller --noconfirm --clean `
    --workpath $workPath `
    --distpath $distPath `
    Bay14Reader.spec

$out = Join-Path $distPath 'Bay14Reader\Bay14Reader.exe'
if (-not (Test-Path -LiteralPath $out)) {
    throw "Build finished but EXE not found at $out"
}

Write-Host ""
Write-Host "Done. Launch:        $out"
Write-Host "Bundle root:         $(Split-Path $out -Parent)"
Write-Host ""
Write-Host "Transfer to offline machine:"
Write-Host "  1. Zip the entire folder $(Split-Path $out -Parent) (keeps Bay14Reader.exe + _internal\ together)."
Write-Host "  2. Copy the zip to the target via USB / share."
Write-Host "  3. Unzip onto a local NTFS path on the target (NOT a Google Drive / OneDrive folder)."
Write-Host "  4. Double-click Bay14Reader.exe."
Write-Host ""
Write-Host "Notes:"
Write-Host "  * Reader writes classic (32-bit) TIFFs, including EXIF tags 306/40962/40963."
Write-Host "  * The 'flip' checkbox next to each camera source folder horizontally"
Write-Host "    mirrors raw frames and rewrites Bayer pattern metadata accordingly."
