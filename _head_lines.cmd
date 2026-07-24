@echo off
setlocal enabledelayedexpansion
set count=0
for /f "usebackq delims=" %%a in ("E:\MemALL\src\memall\pipeline\pipeline.py") do (
    set /a count+=1
    if !count! leq 18 (
        echo !count!: %%a
    )
)
endlocal
