param([string]$ArduinoCli = '')
$ErrorActionPreference = 'Stop'
if (-not $ArduinoCli) {
    $found = Get-Command arduino-cli -ErrorAction SilentlyContinue
    if ($found) { $ArduinoCli = $found.Source }
    else { $ArduinoCli = 'C:\Program Files\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe' }
}
if (-not (Test-Path -LiteralPath $ArduinoCli)) { throw 'Install Arduino CLI or specify -ArduinoCli.' }
$configPath = Join-Path $PSScriptRoot 'arduino-cli.yaml'
if (-not (Test-Path -LiteralPath $configPath)) { throw 'Run build.ps1 -InstallToolchain once to configure the Teensy toolchain.' }
$buildPath = Join-Path $PSScriptRoot 'build-vitality'
& $ArduinoCli compile --config-file $configPath --fqbn teensy:avr:teensy41 --build-path $buildPath (Join-Path $PSScriptRoot 'HubVitality')
if ($LASTEXITCODE) { throw 'Vitality compilation failed.' }
New-Item -ItemType Directory -Path (Join-Path $PSScriptRoot 'release') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $buildPath 'HubVitality.ino.hex') -Destination (Join-Path $PSScriptRoot 'release/HubVitality.ino.hex')
Write-Host 'Built release/HubVitality.ino.hex. No device was programmed.'
