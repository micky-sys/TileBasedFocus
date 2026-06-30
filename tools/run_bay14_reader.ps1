# Launch Bay14Reader GUI from the repo (interactive desktop session).
$ErrorActionPreference = 'Stop'
$Tools = $PSScriptRoot
$HostDir = Join-Path (Split-Path $Tools -Parent) 'host'

$pythonExe = $null
$candidates = @(
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
)
foreach ($c in $candidates) {
    if (Test-Path -LiteralPath $c) { $pythonExe = $c; break }
}
if ($null -eq $pythonExe -and (Get-Command py -ErrorAction SilentlyContinue)) {
    $resolved = & py -3 -c "import sys; print(sys.executable)" 2>$null
    if ($resolved -and (Test-Path -LiteralPath $resolved.Trim())) {
        $pythonExe = $resolved.Trim()
    }
}
if ($null -eq $pythonExe) {
    Write-Host 'No Python 3.11+ found. Install Python or use the built EXE at:'
    Write-Host '  C:\Parallax Apps\Bay14Reader\Bay14Reader.exe'
    Read-Host 'Press Enter to close'
    exit 1
}

$env:PYTHONPATH = "$Tools;$HostDir"
Set-Location -LiteralPath $Tools

Write-Host "Launching Bay14Reader..."
Write-Host "  Python: $pythonExe"
Write-Host "  CWD:    $Tools"
& $pythonExe bay14_reader_gui.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "Exit code: $LASTEXITCODE"
    Read-Host 'Press Enter to close'
}
