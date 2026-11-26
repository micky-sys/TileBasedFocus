param([string]$ArduinoCli = '', [switch]$InstallToolchain)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
if (-not $ArduinoCli) {
    $found = Get-Command arduino-cli -ErrorAction SilentlyContinue
    if ($found) { $ArduinoCli = $found.Source }
    else { $ArduinoCli = 'C:\Program Files\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe' }
}
if (-not (Test-Path -LiteralPath $ArduinoCli)) { throw 'Install Arduino CLI or pass -ArduinoCli with its path.' }
$portableRoot = $root.Replace('\','/')
$configPath = Join-Path $root 'arduino-cli.yaml'
@"
board_manager:
  additional_urls:
    - https://www.pjrc.com/teensy/package_teensy_index.json
directories:
  data: '$portableRoot/.toolchain/data'
  downloads: '$portableRoot/.toolchain/downloads'
  user: '$portableRoot/.toolchain/user'
"@ | Set-Content -LiteralPath $configPath -Encoding utf8
if ($InstallToolchain) {
    & $ArduinoCli core update-index --config-file $configPath
    if ($LASTEXITCODE) { throw 'Could not update package indexes.' }
    & $ArduinoCli core install teensy:avr@1.62.0 --config-file $configPath
    if ($LASTEXITCODE) { throw 'Could not install Teensy 1.62.0.' }
}
& $ArduinoCli compile --config-file $configPath --fqbn teensy:avr:teensy41 --build-path (Join-Path $root 'build') (Join-Path $root 'VibrationLogger')
if ($LASTEXITCODE) { throw 'Compilation failed.' }
New-Item -ItemType Directory -Path (Join-Path $root 'release') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $root 'build/VibrationLogger.ino.hex') -Destination (Join-Path $root 'release/VibrationLogger.ino.hex')
Write-Host 'Built release/VibrationLogger.ino.hex. No device was programmed.'
