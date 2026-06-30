$ErrorActionPreference = 'Stop'
$script = (Resolve-Path (Join-Path $PSScriptRoot 'enable_full_crash_dump.ps1')).Path
Write-Host ('Launching elevated: ' + $script)
$proc = Start-Process powershell `
    -Verb RunAs `
    -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$script) `
    -Wait `
    -PassThru
Write-Host ('Elevated process exited with code: ' + $proc.ExitCode)
