$ErrorActionPreference = 'Stop'
$repo = 'g:\My Drive\Micky\WORK\Cursor\TileBasedFocus'
$bak  = Join-Path $repo '_backups\TileBasedFocus_session_backup_20260617_114031'
$par  = 'C:\Parallax Apps\Iron661 Capture\_internal'

$restored = @()

function Restore-File($rel) {
    $src = Join-Path $bak $rel
    $dst = Join-Path $repo $rel
    if (-not (Test-Path -LiteralPath $src)) {
        throw "Missing backup file: $src"
    }
    $dir = Split-Path $dst -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    Copy-Item -LiteralPath $src -Destination $dst -Force
    $script:restored += $rel
}

# June 17 session snapshot (matches Parallax 17.06 build).
Restore-File 'host\capture\app.py'
Restore-File 'host\capture\camera_manager.py'
Restore-File 'Parallax_CLRCR_V2_4\Parallax_CLRCR_V2_4.ino'

# Bundled datas from the installed exe (authoritative for these three).
foreach ($f in @('serial_comm.py', 'tile_geometry.py', 'sharpness.py')) {
    $src = Join-Path $par $f
    $dst = Join-Path $repo $f
    Copy-Item -LiteralPath $src -Destination $dst -Force
    $restored += "par:$f"
}

Write-Host 'Restored to repo:'
$restored | ForEach-Object { Write-Host "  $_" }

# Verify Parallax root modules now match repo.
$ok = $true
foreach ($f in @('serial_comm.py', 'tile_geometry.py', 'sharpness.py')) {
    $hp = (Get-FileHash (Join-Path $par $f)).Hash
    $hr = (Get-FileHash (Join-Path $repo $f)).Hash
    if ($hp -ne $hr) { Write-Host "VERIFY FAIL: $f"; $ok = $false }
}
if ($ok) { Write-Host 'Verified: root modules match Parallax install.' }
