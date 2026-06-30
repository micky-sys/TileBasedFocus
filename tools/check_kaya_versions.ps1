$ErrorActionPreference = 'SilentlyContinue'

$kaya_root = 'C:\Program Files\KAYA Instruments'

$files = @(
    "$kaya_root\Common\bin\KYFGLib.dll",
    "$kaya_root\Common\bin\KYFGLib_Vc141.dll",
    "$kaya_root\Common\bin\KYFGLib_Vc142.dll",
    "$kaya_root\Common\bin\KYFGLib_Vc143.dll",
    "$kaya_root\Vision Point\VisionPoint.exe",
    "$kaya_root\Vision Point\KAYA Vision Point.exe",
    "$kaya_root\KYVPGenTL\KYVPGenTL.cti",
    "$kaya_root\KYFG\bin\KYVPGenTL.cti"
)

# Also try to find KAYA's kernel driver via Get-Service / Get-CimInstance.
foreach ($f in $files) {
    if (Test-Path $f) {
        $item = Get-Item $f
        $v = $item.VersionInfo
        Write-Host ""
        Write-Host "--- $f ---"
        Write-Host ("FileVersion:    " + $v.FileVersion)
        Write-Host ("ProductVersion: " + $v.ProductVersion)
        Write-Host ("CompanyName:    " + $v.CompanyName)
        Write-Host ("LastModified:   " + $item.LastWriteTime)
    }
}

Write-Host ""
Write-Host "--- KAYA kernel drivers (Get-CimInstance Win32_SystemDriver) ---"
Get-CimInstance Win32_SystemDriver |
    Where-Object { $_.PathName -match 'KAYA' -or $_.Name -match 'Kya|Kaya' } |
    Select-Object Name, DisplayName, State, PathName |
    Format-List

Write-Host ""
Write-Host "--- KAYA *.sys files in DriverStore (last write time) ---"
Get-ChildItem 'C:\Windows\System32\drivers\*.sys' -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '(?i)kya|kaya|fg' } |
    Select-Object Name, LastWriteTime, Length |
    Format-Table -AutoSize

Write-Host ""
Write-Host "--- Installed Programs (KAYA) ---"
Get-CimInstance Win32_Product -ErrorAction SilentlyContinue |
    Where-Object { $_.Vendor -match 'KAYA' -or $_.Name -match 'KAYA' } |
    Select-Object Name, Version, InstallDate |
    Format-Table -AutoSize

Write-Host ""
Write-Host "--- Uninstall registry (KAYA) ---"
$uninstallPaths = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
foreach ($p in $uninstallPaths) {
    Get-ItemProperty $p -ErrorAction SilentlyContinue |
        Where-Object { $_.Publisher -match 'KAYA' -or $_.DisplayName -match 'KAYA' } |
        Select-Object DisplayName, DisplayVersion, Publisher, InstallDate |
        Format-Table -AutoSize
}
