import sys
import os
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = current_dir
while not os.path.exists(os.path.join(project_root, "infrastructure")):
    parent = os.path.dirname(project_root)
    if parent == project_root: break 
    project_root = parent
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import json
import subprocess
import time
import signal
import atexit

# === 配置 ===
KEY_FILE = os.path.join(project_root, "data", "holders_key.json")
RUNTIME_SCRIPT = os.path.join(project_root, "agents", "holder", "runtime.py")
MAX_HOLDERS = 1

# 进程列表，用于清理
processes = []

def cleanup_processes():
    """清理所有子进程"""
    print(f"\n[Manager] Stopping {len(processes)} holder processes...")
    for p in processes:
        if p.poll() is None:  # 如果进程还在运行
            p.terminate()
    print("[Manager] All processes stopped.")

def signal_handler(sig, frame):
    """处理 Ctrl+C"""
    cleanup_processes()
    sys.exit(0)

def main():
    # 注册退出清理
    atexit.register(cleanup_processes)
    signal.signal(signal.SIGINT, signal_handler)

    print("="*60)
    print("MASSIVE P2P HOLDER LAUNCHER")
    print("="*60)

    # 1. 读取密钥文件
    if not os.path.exists(KEY_FILE):
        print(f"[Error] Key file not found: {KEY_FILE}")
        return

    with open(KEY_FILE, 'r', encoding='utf-8') as f:
        config_data = json.load(f)
    
    accounts = config_data.get("accounts", {})
    
    # 2. 筛选 holder_X_op 角色并按数字排序
    # 假设 key 格式为 "holder_1_op", "holder_2_op"
    holder_roles = [k for k in accounts.keys() if "_op" in k and "holder" in k]
    try:
        holder_roles.sort(key=lambda x: int(x.split('_')[1]))
    except:
        holder_roles.sort()

    # 强制截取前 MAX_HOLDERS 个
    holder_roles = holder_roles[:MAX_HOLDERS] 

    total_holders = len(holder_roles)
    print(f"[Manager] Found {total_holders} holder accounts to launch.")

    # 3. 批量启动
    for i, role in enumerate(holder_roles):
        port = 5000 + i
        
        # 命令格式: python runtime.py <PORT> <ROLE> <KEY_FILE_PATH>
        cmd = [
            sys.executable, 
            RUNTIME_SCRIPT, 
            str(port), 
            role, 
            KEY_FILE  # 传入 key file 路径，触发 runtime.py 的自定义加载逻辑
        ]
        
        # 启动进程 (stdout/stderr 设为 DEVNULL 可以减少噪音，或者保留以调试)
        # 建议重定向到文件或设为 DEVNULL，否则 100 个进程的日志会卡死终端
        try:
            p = subprocess.Popen(
                cmd, 
                cwd=project_root,
                # stdout=subprocess.DEVNULL, 
                # stderr=subprocess.DEVNULL
            )
            processes.append(p)
            print(f"[{i+1}/{total_holders}] Started {role} on port {port} (PID: {p.pid})")
        except Exception as e:
            print(f"[Error] Failed to start {role}: {e}")

        # 稍微间隔一下，避免瞬间 CPU 峰值
        if (i + 1) % 10 == 0:
            time.sleep(1)

    print("="*60)
    print(f"[Manager] All {len(processes)} holders are running.")
    print("[Manager] Press Ctrl+C to stop all agents.")
    
    # 4. 保持主进程存活
    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()
