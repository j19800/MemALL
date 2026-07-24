#!/usr/bin/env python3
"""MemALL — 一键安装部署脚本
下载后运行: python install.py
自动完成: 安装依赖 → 初始化数据库 → 启动服务 → 配置MCP → 打开浏览器
"""

import os, sys, subprocess, time, webbrowser, json
from pathlib import Path

def step(msg):
    print(f"\n[{msg}]")

def run(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)

print("=" * 55)
print("  MemALL — 一键部署")
print("=" * 55)

# 1. Install dependencies
step("1/5 安装依赖")
result = run(f"{sys.executable} -m pip install memall-os -q")
if result.returncode != 0:
    # If pip install fails, install from local
    repo = Path(__file__).parent
    result = run(f"{sys.executable} -m pip install -r {repo / 'requirements.txt'} -q")
print("  依赖安装完成")

# 2. Init database
step("2/5 初始化数据库")
try:
    from memall.core.db import init_db
    init_db(migrate=True)
    print("  数据库初始化完成")
except Exception as e:
    print(f"  数据库初始化失败: {e}")
    sys.exit(1)

# 3. Start gateway in background
step("3/5 启动服务")
port = 9920
startup_script = f'''
import sys, time
sys.path.insert(0, r"{Path(__file__).parent / 'src'}")
from memall.gateway import MemAllGateway
gw = MemAllGateway(host="127.0.0.1", port={port})
gw.start()
print("Gateway running", flush=True)
while True:
    time.sleep(10)
'''

# Kill any existing gateway on this port
import socket
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
if sock.connect_ex(('127.0.0.1', port)) == 0:
    run(f"taskkill /F /IM python.exe /FI \"WINDOWTITLE eq memall*\" 2>nul", timeout=3)
sock.close()
time.sleep(1)

# Start in background
DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000
proc = subprocess.Popen(
    [sys.executable, "-c", startup_script],
    creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
    close_fds=True,
)
time.sleep(3)
print(f"  服务启动: http://127.0.0.1:{port}")

# 4. Configure MCP
step("4/5 配置MCP")
try:
    from memall.cli.commands.management_commands import cmd_setup as _cmd_setup
    class _Args:
        all_agents = True
        agent = None
        fix = False
        config = None
        user = "admin"
        db = None
    _cmd_setup(_Args())
    print("  MCP配置完成")
except Exception as e:
    print(f"  MCP配置跳过: {e}")

# 5. Auto-start on boot
step("5/5 开机自启")
startup_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
if startup_dir.exists():
            bat_path = startup_dir / "memall_gateway.bat"
            bat_path.write_text(
                f'@echo off\n"{sys.executable}" -c "{startup_script.replace(chr(34), chr(39))}"\n',
                encoding="utf-8",
            )
            print(f"  开机自启已添加: {bat_path}")
else:
            print("  开机自启跳过")

# Done
print("\n" + "=" * 55)
print("  MemALL 部署完成!")
print(f"  前端:   http://127.0.0.1:{port}")
print(f"  MCP:    http://127.0.0.1:{port}/mcp")
print("=" * 55)

# Open browser
try:
    webbrowser.open(f"http://127.0.0.1:{port}")
except Exception:
    pass