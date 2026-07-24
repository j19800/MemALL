$out = "E:\MemALL\conversations\nomi-temp-ws_019f88ab-655e-7292-baae-6f5818ac1eff\env2.log"
$enc = [System.Text.UTF8Encoding]::new($false)

# Clear file
[System.IO.File]::WriteAllText($out, "", $enc)

function Log {
    param([string]$s)
    [System.IO.File]::AppendAllText($out, $s + "`r`n", [System.Text.UTF8Encoding]::new($false))
    Write-Host $s
}

Log "=== OS ==="
Log ([Environment]::OSVersion.ToString())
Log "=== ARCH ==="
Log ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString())
Log "=== NODE ==="
try { $v = node --version 2>&1; Log $v } catch { Log "ERROR: $_" }
Log "=== NPM ==="
try { $v = npm --version 2>&1; Log $v } catch { Log "ERROR: $_" }
Log "=== GIT ==="
try { $v = git --version 2>&1; Log $v } catch { Log "ERROR: $_" }
Log "=== CURL ==="
try { $v = curl --version 2>&1; if ($v -is [array]) { $v = $v[0] }; Log $v } catch { Log "ERROR: $_" }
Log "=== CURL.EXE ==="
try { $c = Get-Command curl.exe -ErrorAction SilentlyContinue; if ($c) { Log $c.Source } else { Log "NOT_FOUND" } } catch { Log "NOT_FOUND" }
Log "=== DONE ==="
