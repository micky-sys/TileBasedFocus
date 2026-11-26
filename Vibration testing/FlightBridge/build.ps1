$ErrorActionPreference = 'Stop'
# Keep PyInstaller's build outputs on local NTFS; copy the finished EXE to Drive.
$localBuildRoot = Join-Path $env:LOCALAPPDATA 'XParallaxBuild\FlightBridge'
$localDist = Join-Path $localBuildRoot 'dist'
$localWork = Join-Path $localBuildRoot 'build'
$localExe = Join-Path $localDist 'FlightBridge.exe'
$releaseDir = Join-Path $PSScriptRoot 'release'
$releaseExe = Join-Path $releaseDir 'FlightBridge.exe'
Push-Location $PSScriptRoot
try {
    python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
    python -m PyInstaller --noconfirm --onefile --windowed --name FlightBridge --distpath $localDist --workpath $localWork --specpath $localBuildRoot (Join-Path $PSScriptRoot 'app.py')
    if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
    New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
    Copy-Item -LiteralPath $localExe -Destination $releaseExe -Force
    if ((Get-FileHash -LiteralPath $localExe -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $releaseExe -Algorithm SHA256).Hash) {
        throw 'Release copy failed SHA256 verification.'
    }
    Write-Host "Built and verified $releaseExe"
} finally {
    Pop-Location
}
