# Find large image / video files on C: that are wasting space.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File tools\find_big_images_on_c.ps1

$exts = '*.tif','*.tiff','*.jpg','*.jpeg','*.png','*.bmp','*.raw','*.dng','*.cr2','*.nef','*.arw','*.heic','*.mp4','*.mov','*.avi','*.mkv'
$minBytes = 10MB

# Scope to user-writable trees only. The whole-of-C: walk hit
# UnauthorizedAccessException on a protected ACL and bailed before
# returning matches; -ErrorAction SilentlyContinue is supposed to
# swallow those but PowerShell streams them through the pipeline and
# can abort on a non-skippable ACL violation.
$roots = @(
    'C:\Users'
    'C:\ProgramData'
    'C:\Temp'
    'C:\tmp'
    'C:\Cursor'        # in case there's a project tree at root
    'C:\Pictures'
    'C:\Photos'
    'C:\Data'
    'C:\Downloads'
    'C:\KAYA'
)

$skipPaths = @(
    'C:\Users\Default'
    'C:\Users\Public\AccountPictures'
    'C:\Users\All Users'
)

Write-Host "Scanning user-writable C: trees for image/video files >= 10 MB..."
Write-Host ""

$found = New-Object System.Collections.Generic.List[System.IO.FileInfo]

foreach ($root in $roots) {
    if (-not (Test-Path -LiteralPath $root)) { continue }
    Write-Host ("  scanning {0} ..." -f $root)
    try {
        # -Force = include hidden, -ErrorAction SilentlyContinue to skip
        # ACL-protected subfolders without aborting the whole walk.
        $items = Get-ChildItem -LiteralPath $root -Include $exts -File -Recurse `
            -Force -ErrorAction SilentlyContinue
        foreach ($it in $items) {
            if ($it.Length -lt $minBytes) { continue }
            $p = $it.FullName
            $skip = $false
            foreach ($s in $skipPaths) { if ($p.StartsWith($s)) { $skip = $true; break } }
            if ($skip) { continue }
            [void]$found.Add($it)
        }
    } catch {
        Write-Host ("    (error in {0}: {1})" -f $root, $_.Exception.Message)
    }
}

if ($found.Count -eq 0) {
    Write-Host ""
    Write-Host "No matches found in scanned roots."
    return
}

$sorted = $found | Sort-Object Length -Descending
Write-Host ""
Write-Host ("Top 50 (of {0}) by size:" -f $found.Count)

$sorted | Select-Object -First 50 | ForEach-Object {
    $sz = [math]::Round($_.Length/1MB, 1)
    "{0,8} MB  {1}" -f $sz, $_.FullName
}

$totalGB = [math]::Round((($found | Measure-Object Length -Sum).Sum) / 1GB, 2)
Write-Host ""
Write-Host ("Total: {0} files, {1} GB across all matches." -f $found.Count, $totalGB)

Write-Host ""
Write-Host "By folder (top 15):"
$found |
    Group-Object { Split-Path $_.FullName -Parent } |
    ForEach-Object {
        $sumBytes = ($_.Group | Measure-Object Length -Sum).Sum
        [pscustomobject]@{
            Folder = $_.Name
            Count  = $_.Count
            GB     = [math]::Round($sumBytes/1GB, 2)
        }
    } |
    Sort-Object GB -Descending |
    Select-Object -First 15 |
    Format-Table -AutoSize
