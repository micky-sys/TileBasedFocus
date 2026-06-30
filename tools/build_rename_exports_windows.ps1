# Build portable Windows one-file CLI for the export renamer (PyInstaller).
#
# IMPORTANT: PyInstaller MUST write its EXE to a real local NTFS disk. Writing
# to a virtual / streamed filesystem (Google Drive for Desktop "G:\My Drive\..",
# OneDrive on-demand, Dropbox Smart Sync, etc.) silently corrupts the embedded
# PKG archive bytes inside the .exe -- the build "succeeds" but the bootloader
# pops "Could not load PyInstaller's embedded PKG archive..." at launch.
# Fix is structural: always redirect --workpath / --distpath to C:\.
#
# Usage (PowerShell):
#   cd ...\TileBasedFocus\tools
#   .\build_rename_exports_windows.ps1                 # default local build root
#   .\build_rename_exports_windows.ps1 -BuildRoot D:\Build\RenameExports
#
# Output: <BuildRoot>\dist\RenameExports.exe   (single-file CLI)
[CmdletBinding()]
param(
    [string]$BuildRoot = "$env:LOCALAPPDATA\RenameExportsBuild"
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Test-VirtualFsPath {
    param([string]$Path)
    $full = [IO.Path]::GetFullPath($Path)
    return ($full -imatch '\\(My Drive|OneDrive[^\\]*|Dropbox)(\\|$)')
}
if (Test-VirtualFsPath -Path $BuildRoot) {
    throw "BuildRoot '$BuildRoot' is on a virtual/streamed filesystem (Google Drive / OneDrive / Dropbox). PyInstaller PKG embedding will corrupt the EXE there. Choose a real local NTFS path, e.g. C:\Build\RenameExports."
}
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
$workPath = Join-Path $BuildRoot 'build'
$distPath = Join-Path $BuildRoot 'dist'

Write-Host "Installing PyInstaller ..."
py -3 -m pip install --upgrade pyinstaller

Write-Host "Running PyInstaller (--clean) ..."
Write-Host "  workpath: $workPath"
Write-Host "  distpath: $distPath"
py -3 -m PyInstaller --noconfirm --clean `
    --workpath $workPath `
    --distpath $distPath `
    RenameExports.spec

$out = Join-Path $distPath 'RenameExports.exe'
if (-not (Test-Path -LiteralPath $out)) {
    throw "Build finished but EXE not found at $out"
}

Write-Host ""
Write-Host "Done. EXE: $out"
Write-Host ""
Write-Host "Example usage on the target machine (copy RenameExports.exe to a local path first):"
Write-Host "  RenameExports.exe D:\Exports --dry-run"
Write-Host "  RenameExports.exe D:\Exports"
Write-Host "  RenameExports.exe D:\A D:\B --extensions tif jpg -v"
