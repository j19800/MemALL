# MemALL Gateway — 后台自动启动 (以隐藏窗口运行)
# 用法: powershell -WindowStyle Hidden -File start_gateway.ps1

$repoDir = "E:\MemALL"
$pythonScript = @"
import sys, time
sys.path.insert(0, '$repoDir\src'.replace('\\', '/'))
from memall.gateway import MemAllGateway
gw = MemAllGateway(host='127.0.0.1', port=9920)
gw.start()
print('MemALL Gateway running on http://127.0.0.1:9920', flush=True)
while True:
    time.sleep(10)
"@

Set-Location $repoDir
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = "python"
$psi.Arguments = "-c `"$pythonScript`""
$psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
$psi.CreateNoWindow = $true
[System.Diagnostics.Process]::Start($psi) | Out-Null
Start-Sleep -Seconds 3
Write-Host "MemALL Gateway started on http://127.0.0.1:9920"