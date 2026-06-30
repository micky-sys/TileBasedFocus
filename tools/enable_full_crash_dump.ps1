# Configure Windows to preserve the next BSOD memory dump.
#
# Background:
#   The KAYA driver BSOD'd with DRIVER_VERIFIER_DMA_VIOLATION (0xE6) but
#   no MEMORY.DMP was found in C:\Windows\. Investigation showed:
#       CrashDumpEnabled    = 7   (Automatic memory dump -- already correct)
#       AlwaysKeepMemoryDump =     (empty -- Windows may silently delete
#                                   the dump after successful auto-upload)
#       AutoReboot          = 1   (reboots before user can grab the file)
#
# This script flips the one missing setting (AlwaysKeepMemoryDump = 1) so
# the next dump survives reboot, and reports the final state.
#
# Run from an *elevated* PowerShell:
#       powershell -NoProfile -ExecutionPolicy Bypass -File tools\enable_full_crash_dump.ps1

$ErrorActionPreference = 'Continue'

# Mirror everything to a log file so we can diagnose silent failures
# (UAC declined, group policy refusing the registry write, etc.) when
# the script is launched via Start-Process -Verb RunAs and the elevated
# console flashes by too fast to read.
$log_path = Join-Path $PSScriptRoot 'enable_full_crash_dump.log'
Start-Transcript -Path $log_path -Force | Out-Null

try {
    Write-Host ("Started: " + (Get-Date -Format o))
    Write-Host ("PSCommandPath: " + $PSCommandPath)
    Write-Host ("WhoAmI: " + (& whoami))

    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $pri = New-Object Security.Principal.WindowsPrincipal($id)
    $isAdmin = $pri.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    Write-Host ("Admin: " + $isAdmin)
    if (-not $isAdmin) {
        Write-Host "ERROR: not elevated. Right-click PowerShell -> Run as administrator, then re-run this script."
        Stop-Transcript | Out-Null
        exit 1
    }

    $key = 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl'

    Write-Host "BEFORE:"
    Get-ItemProperty -Path $key |
        Select-Object CrashDumpEnabled, AlwaysKeepMemoryDump, AutoReboot, DumpFile |
        Format-List

    # 7 = Automatic memory dump (Windows manages pagefile + writes a dump
    # that is roughly equivalent to a kernel dump but with paged memory
    # the OS considers relevant to the crash).
    Set-ItemProperty -Path $key -Name CrashDumpEnabled    -Type DWord -Value 7
    # 1 = never auto-delete the dump (default 0 = Windows can remove it
    # after successful Watson upload, which is why our previous BSOD
    # left no file).
    Set-ItemProperty -Path $key -Name AlwaysKeepMemoryDump -Type DWord -Value 1

    Write-Host ""
    Write-Host "AFTER:"
    Get-ItemProperty -Path $key |
        Select-Object CrashDumpEnabled, AlwaysKeepMemoryDump, AutoReboot, DumpFile |
        Format-List

    $final_dump = (Get-ItemProperty $key).DumpFile
    Write-Host "Done. Next BSOD will leave $final_dump in place."
}
catch {
    Write-Host "EXCEPTION:"
    Write-Host $_.ToString()
    Write-Host $_.ScriptStackTrace
}
finally {
    Stop-Transcript | Out-Null
}
