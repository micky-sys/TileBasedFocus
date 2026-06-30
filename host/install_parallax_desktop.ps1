# Copy PyInstaller output to the default install folder and add a desktop shortcut.
#
# IMPORTANT: PyInstaller must build onto real local NTFS, never directly into the
# Google-Drive copy of the repo, or the produced EXE hits the "Could not load
# PyInstaller's embedded PKG archive" popup at launch (see docs/pkg_issue.md).
# Build with:
#   $r = Join-Path $env:LOCALAPPDATA 'Iron661Build'
#   py -3 -m PyInstaller --noconfirm --clean --distpath "$r\dist" --workpath "$r\build" Iron661Capture.spec
# This installer therefore prefers the local build output and only falls back to
# the on-Drive dist\ (which may be PKG-corrupted) if no local build exists.
$ErrorActionPreference = 'Stop'
$dst = 'C:\Parallax Apps\Iron661 Capture'

$localDir = Join-Path $env:LOCALAPPDATA 'Iron661Build\dist\Iron661Capture'
$driveDir = Join-Path $PSScriptRoot 'dist\Iron661Capture'
if (Test-Path -LiteralPath (Join-Path $localDir 'Iron661Capture.exe')) {
    $oneDir = $localDir
    Write-Host "Source: local build  ($localDir)"
} else {
    $oneDir = $driveDir
    Write-Warning "Local build not found; falling back to on-Drive dist (risk of PKG-archive popup): $driveDir"
}
$oneExe = Join-Path $PSScriptRoot 'dist\Iron661Capture.exe'
$onedirExe = Join-Path $oneDir 'Iron661Capture.exe'

New-Item -ItemType Directory -Force -Path $dst | Out-Null

# Prefer onedir: avoids one-file PKG extraction failures at launch (AV / TEMP).
if (Test-Path -LiteralPath $onedirExe) {
    robocopy $oneDir $dst /MIR /R:2 /W:2 | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed with exit code $LASTEXITCODE"
    }
    Write-Host 'Installed onedir bundle from dist\Iron661Capture\.'
}
elseif (Test-Path -LiteralPath $oneExe) {
    Copy-Item -LiteralPath $oneExe -Destination (Join-Path $dst 'Iron661Capture.exe') -Force
    Write-Host 'Installed Iron661Capture.exe (legacy one-file build).'
}
else {
    Write-Error @"
Missing build. From host\, run:
  .\build_windows_release.ps1
Expected $onedirExe or $oneExe
"@
}

$exe = Join-Path $dst 'Iron661Capture.exe'
if (-not (Test-Path -LiteralPath $exe)) {
    Write-Error "Executable not found after install: $exe"
}

$desk = [Environment]::GetFolderPath('Desktop')
$lnkPath = Join-Path $desk 'Parallax Iron661 Capture.lnk'
$w = New-Object -ComObject WScript.Shell
$sc = $w.CreateShortcut($lnkPath)
$sc.TargetPath = $exe
$sc.WorkingDirectory = $dst
$sc.Description = 'Iron 661 Capture + Focus'
$sc.Save()
Write-Host "Installed: $dst"
Write-Host "Shortcut:  $lnkPath"
# robocopy uses 0–7 for success; PowerShell would otherwise propagate 1 as script exit.
exit 0
