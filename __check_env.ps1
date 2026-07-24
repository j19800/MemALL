$envFile = "E:\memall\.env"
if (Test-Path $envFile) {
    Write-Host "=== .env FILE EXISTS ==="
    Get-Content $envFile
} else {
    Write-Host "NO .ENV FILE"
}

$proc = Get-CimInstance Win32_Process -Filter "ProcessId=20236" | Select-Object Name, CommandLine
if ($proc) {
    Write-Host "=== PROCESS 20236 ==="
    $proc | Format-List
} else {
    Write-Host "PROCESS 20236 NOT FOUND"
}
Write-Host "=== DONE ==="
