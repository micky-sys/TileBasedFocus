param([string]$ArduinoCli = '')
$ErrorActionPreference = 'Stop'
if (-not $ArduinoCli) {
    $found = Get-Command arduino-cli -ErrorAction SilentlyContinue
    if ($found) { $ArduinoCli = $found.Source }
    else { $ArduinoCli = 'C:\Program Files\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe' }
}
if (-not (Test-Path -LiteralPath $ArduinoCli)) { throw 'Install Arduino CLI or specify -ArduinoCli.' }
$buildPath = Join-Path $PSScriptRoot 'build-csvlogger'
& $ArduinoCli compile --config-file (Join-Path $PSScriptRoot 'arduino-cli.yaml') --fqbn teensy:avr:teensy41 --build-path $buildPath (Join-Path $PSScriptRoot 'ResetCsvLogger')
if ($LASTEXITCODE) { throw 'ResetCsvLogger compilation failed.' }
New-Item -ItemType Directory -Path (Join-Path $PSScriptRoot 'release') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $buildPath 'ResetCsvLogger.ino.hex') -Destination (Join-Path $PSScriptRoot 'release/ResetCsvLogger.ino.hex')
Write-Host 'Built release/ResetCsvLogger.ino.hex. No device was programmed.'
