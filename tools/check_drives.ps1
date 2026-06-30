$proj = if ($args.Count -gt 0) { $args[0] } else { 'test' }
# Sweep frames are written as raw .bay14 + .bay14.json sidecar pairs
# (see host/capture/raw_bay_writer.py). We only count the .bay14 files
# here so the sidecars don't double the count.
foreach ($drv in @('D','E','F')) {
    Write-Host ('--- ' + $drv + ':\' + $proj + ' ---')
    $path = ($drv + ':\' + $proj)
    $files = Get-ChildItem -Path $path -Filter '*.bay14' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime
    Write-Host ('count: ' + $files.Count)
    if ($files.Count -gt 0) {
        Write-Host 'first 3:'
        $files | Select-Object -First 3 Name, Length, LastWriteTime | Format-Table -AutoSize
        Write-Host 'last 3:'
        $files | Select-Object -Last 3 Name, Length, LastWriteTime | Format-Table -AutoSize
        $total_gb = [math]::Round(($files | Measure-Object Length -Sum).Sum / 1GB, 2)
        Write-Host ('total: ' + $total_gb + ' GB')
    }
}
