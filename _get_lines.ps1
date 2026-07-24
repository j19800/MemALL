$lines = Get-Content -Path "E:\MemALL\src\memall\pipeline\pipeline.py" -TotalCount 18
$i = 1
foreach ($l in $lines) {
    Write-Output "$i : $l"
    $i++
}
