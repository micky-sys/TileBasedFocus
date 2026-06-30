foreach ($drv in @('D','E','F')) {
    $bp = ($drv + ':\bench_writer')
    $tp = ($drv + ':\test')
    if (Test-Path $bp) {
        Remove-Item $bp -Recurse -Force
        Write-Host "removed $bp"
    }
    if (Test-Path $tp) {
        Remove-Item $tp -Recurse -Force
        Write-Host "removed $tp"
    }
}
