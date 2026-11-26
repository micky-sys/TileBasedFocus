$ErrorActionPreference = 'Stop'
$buildOriginalPath = $env:PATH
$buildPython = (Get-Command python).Source
$buildPythonDir = Split-Path -Parent $buildPython
# Keep unrelated application DLLs off the resolver PATH.
$env:PATH = "$buildPythonDir;$buildPythonDir\Scripts;$env:SystemRoot\System32;$env:SystemRoot"
# PyInstaller must write its binary archive to local NTFS, not streamed Drive.
$localBuildRoot = Join-Path $env:LOCALAPPDATA 'XParallaxBuild\VibrationMonitor'
$localDist = Join-Path $localBuildRoot 'dist'
$localWork = Join-Path $localBuildRoot 'build'
$localExe = Join-Path $localDist 'VibrationMonitor.exe'
$releaseDir = Join-Path $PSScriptRoot 'release'
$releaseExe = Join-Path $releaseDir 'VibrationMonitor.exe'
Push-Location $PSScriptRoot
try {
    python -m unittest discover -s tests -v
    if ($LASTEXITCODE) { throw 'Tests failed.' }
    python tests/ui_test.py
    if ($LASTEXITCODE) { throw 'GUI workflow tests failed.' }
    python -m PyInstaller --clean --noconfirm --distpath $localDist --workpath $localWork VibrationMonitor.spec
    if ($LASTEXITCODE) { throw 'Windows build failed.' }
    python tests/packaged_test.py $localExe
    if ($LASTEXITCODE) { throw 'Local packaged app smoke test failed.' }
    New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
    Copy-Item -LiteralPath $localExe -Destination $releaseExe -Force
    if ((Get-FileHash -LiteralPath $localExe -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $releaseExe -Algorithm SHA256).Hash) {
        throw 'Release copy failed SHA256 verification.'
    }
    python tests/packaged_test.py $releaseExe
    if ($LASTEXITCODE) { throw 'Copied packaged app smoke test failed.' }
    Write-Host "Built and verified $releaseExe"
} finally { Pop-Location; $env:PATH = $buildOriginalPath }
