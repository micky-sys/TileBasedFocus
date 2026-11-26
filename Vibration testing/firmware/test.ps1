param([string]$Python = 'python', [string]$Cpp = 'g++')
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    New-Item -ItemType Directory -Path build -Force | Out-Null
    & $Cpp -std=c++17 -O2 -Wall -Wextra -pthread tests/core_test.cpp -o build/core_test.exe
    if ($LASTEXITCODE) { throw 'Native test build failed.' }
    & ./build/core_test.exe ./build/test-fixture.XPL
    if ($LASTEXITCODE) { throw 'Native tests failed.' }
    & $Cpp -std=c++17 -O2 -Wall -Wextra tests/spi_test.cpp -o build/spi_test.exe
    if ($LASTEXITCODE) { throw 'SPI diagnostic test build failed.' }
    & ./build/spi_test.exe
    if ($LASTEXITCODE) { throw 'SPI diagnostic tests failed.' }
    & $Cpp -std=c++17 -O2 -Wall -Wextra tests/vitality_test.cpp -o build/vitality_test.exe
    if ($LASTEXITCODE) { throw 'Vitality test build failed.' }
    & ./build/vitality_test.exe
    if ($LASTEXITCODE) { throw 'Vitality tests failed.' }
    & $Cpp -std=c++17 -O2 -Wall -Wextra -pthread tests/csvlogger_test.cpp -o build/csvlogger_test.exe
    if ($LASTEXITCODE) { throw 'CSV logger test build failed.' }
    & ./build/csvlogger_test.exe
    if ($LASTEXITCODE) { throw 'CSV logger tests failed.' }
    & $Cpp -std=c++17 -O2 -Wall -Wextra tests/dual_csv_test.cpp -o build/dual_csv_test.exe
    if ($LASTEXITCODE) { throw 'Dual CSV logger test build failed.' }
    & ./build/dual_csv_test.exe
    if ($LASTEXITCODE) { throw 'Dual CSV logger tests failed.' }
    & $Python -m unittest discover -s tests -v
    if ($LASTEXITCODE) { throw 'Decoder tests failed.' }
} finally { Pop-Location }
