$out = "env_results.log"

"=== OS ===" | Out-File $out
[Environment]::OSVersion | Out-File $out -Append

"=== ARCH ===" | Out-File $out -Append
[System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture | Out-File $out -Append

"=== NODE ===" | Out-File $out -Append
$v = & node --version 2>&1
if ($LASTEXITCODE -eq 0) { "v$v" | Out-File $out -Append } else { "NOT_FOUND" | Out-File $out -Append }

"=== NPM ===" | Out-File $out -Append
$v = & npm --version 2>&1
if ($LASTEXITCODE -eq 0) { $v | Out-File $out -Append } else { "NOT_FOUND" | Out-File $out -Append }

"=== GIT ===" | Out-File $out -Append
$v = & git --version 2>&1
if ($LASTEXITCODE -eq 0) { $v | Out-File $out -Append } else { "NOT_FOUND" | Out-File $out -Append }

"=== CURL.EXE ===" | Out-File $out -Append
$c = Get-Command curl.exe -ErrorAction SilentlyContinue
if ($c) { $c.Source | Out-File $out -Append } else { "NOT_FOUND" | Out-File $out -Append }

"=== POWERSHELL ===" | Out-File $out -Append
$PSVersionTable.PSVersion | Out-File $out -Append

"=== PATH (node related) ===" | Out-File $out -Append
$env:PATH -split ';' | Where-Object { $_ -match 'node|npm|git|curl' } | Out-File $out -Append

"=== DONE ===" | Out-File $out -Append
Write-Host "Results written to $out"
