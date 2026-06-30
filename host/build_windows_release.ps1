# Build an offline-capable Windows app (PyInstaller onedir — avoids PKG unpack issues).
#
# Usage (PowerShell):
#   cd ...\TileBasedFocus\host
#   .\build_windows_release.ps1
#
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

Write-Host "Installing runtime + PyInstaller..."
py -3 -m pip install -r requirements.txt -r requirements-build.txt

Write-Host "Building Windows icon (.ico) from assets/Iron661_logo.png..."
py -3 tools\make_exe_icon.py

Write-Host "Running PyInstaller (--clean)..."
py -3 -m PyInstaller --noconfirm --clean Iron661Capture.spec

# PyInstaller onedir writes ``dist\Iron661Capture\Iron661Capture.exe``. An older
# one-file build may have left ``dist\Iron661Capture.exe`` at the dist root;
# that copy trips "embedded PKG" errors if launched by mistake — remove it.
$staleRootExe = Join-Path $PSScriptRoot 'dist\Iron661Capture.exe'
if (Test-Path -LiteralPath $staleRootExe) {
    Remove-Item -LiteralPath $staleRootExe -Force
    Write-Host "Removed stale dist\Iron661Capture.exe (use dist\Iron661Capture\ subfolder)."
}

$out = Join-Path $PSScriptRoot 'dist\Iron661Capture\Iron661Capture.exe'
Write-Host ""
Write-Host "Done. Launch: $out"
Write-Host "(Folder install: $(Join-Path $PSScriptRoot 'dist\Iron661Capture'))"
