# Copy PyInstaller onedir output to C:\Parallax and add a desktop shortcut.
#
# Usage (from tools/, after build):
#   .\install_bay14_reader_parallax.ps1
#
$ErrorActionPreference = 'Stop'
$dst = 'C:\Parallax'
$src = Join-Path $PSScriptRoot 'dist\Bay14Reader'
$exeName = 'Bay14Reader.exe'

New-Item -ItemType Directory -Force -Path $dst | Out-Null

if (-not (Test-Path -LiteralPath (Join-Path $src $exeName))) {
    Write-Error @"
Missing build. From tools\, run:
  .\build_bay14_reader_windows.ps1
Expected $(Join-Path $src $exeName)
"@
}

robocopy $src $dst /MIR /R:2 /W:2 | Out-Null
if ($LASTEXITCODE -ge 8) {
    throw "robocopy failed with exit code $LASTEXITCODE"
}

$exe = Join-Path $dst $exeName
if (-not (Test-Path -LiteralPath $exe)) {
    Write-Error "Executable not found after install: $exe"
}

$desk = [Environment]::GetFolderPath('Desktop')
$lnkPath = Join-Path $desk 'Bay14 Reader.lnk'
$w = New-Object -ComObject WScript.Shell
$sc = $w.CreateShortcut($lnkPath)
$sc.TargetPath = $exe
$sc.WorkingDirectory = $dst
$sc.Description = 'Bay14 line batch reader / exporter'
$sc.Save()

Write-Host "Installed: $dst"
Write-Host "Shortcut:  $lnkPath"
Write-Host "To move to another PC: zip the entire C:\Parallax folder (exe + _internal)."

exit 0
