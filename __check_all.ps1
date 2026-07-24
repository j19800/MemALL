$ErrorActionPreference = "Stop"

# 1. Check port 9876
$result1 = netstat -ano | Select-String ":9876"
"=== PORT 9876 ===" > E:\memall\__check_result.txt
$result1.ToString() >> E:\memall\__check_result.txt

# 2. Check port 8199 (memall default)
$result2 = netstat -ano | Select-String ":8199"
"=== PORT 8199 ===" >> E:\memall\__check_result.txt
if ($result2) { $result2.ToString() >> E:\memall\__check_result.txt } else { "None" >> E:\memall\__check_result.txt }

# 3. Check if memall command is available
"=== MEMALL HELP ===" >> E:\memall\__check_result.txt
$help = & cmd /c "C:\Users\Administrator\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe -m memall serve --http --help" 2>&1
$help -join "`n" >> E:\memall\__check_result.txt

# 4. Check mcp connect help
"=== MCP CONNECT ===" >> E:\memall\__check_result.txt
$mcpHelp = & cmd /c "C:\Users\Administrator\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe -m memall mcp connect --help" 2>&1
$mcpHelp -join "`n" >> E:\memall\__check_result.txt

# 5. Check server.log
"=== SERVER.LOG TAIL ===" >> E:\memall\__check_result.txt
if (Test-Path E:\memall\server.log) {
    Get-Content E:\memall\server.log -Tail 30 >> E:\memall\__check_result.txt
} else { "No server.log" >> E:\memall\__check_result.txt }

Write-Host "Done"
