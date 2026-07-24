@echo off
REM MemALL Gateway — 后台自动启动脚本
REM 将此脚本放入 shell:startup 即可开机自启

cd /d "E:\MemALL"
start /B python -c "import sys, time; sys.path.insert(0, 'src'); from memall.gateway import MemAllGateway; gw = MemAllGateway(host='127.0.0.1', port=9920); gw.start(); print('MemALL Gateway running on http://127.0.0.1:9920', flush=True); [time.sleep(10) for _ in range(1000000)]"
timeout /t 3 /nobreak >nul
echo MemALL Gateway started on http://127.0.0.1:9920