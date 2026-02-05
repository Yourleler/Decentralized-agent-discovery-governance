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

def start_network():
    # 1. 读取配置
    json_path = os.path.join(project_root, 'config', 'network_config.json')

    try:
        with open(json_path, "r", encoding='utf-8') as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"Error: Config file not found at {json_path}")
        return

    processes = []

    print("="*60)
    print("Initializing Decentralized Network Simulation")
    print("="*60)

    # 2. 启动所有 Holders
    for h in config["holders"]:
        print(f"Starting [Holder] {h['name']} ({h['role']}) on port {h['port']}...")
        
        # 构造命令: python agents/holder/runtime.py <port> <role>
        cmd = [
            sys.executable, 
            "agents/holder/runtime.py", 
            str(h["port"]), 
            h["role"]
        ]
        
        # 使用 new_console=True (Windows) 可以让每个进程弹出一个新窗口，方便看日志
        # 如果不想弹窗，可以去掉 creationflags，但日志会混在一起
        p = subprocess.Popen(
            cmd, 
            #creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == 'win32' else 0
        )
        processes.append(p)

    # 等待几秒，确保 Holder 启动完成
    time.sleep(2) 

    # 3. 启动所有 Verifiers
    for v in config["verifiers"]:
        print(f"Starting [Verifier] {v['name']} ({v['role']}) on port {v['port']} -> target {v['target_url']}...")
        
        # 构造命令: python _demo_2v2/demo_verifier_server.py <port> <role> <target>
        cmd = [
            sys.executable, 
            "_demo_2v2/demo_verifier_server.py", 
            str(v["port"]), 
            v["role"],
            v["target_url"]
        ]
        
        p = subprocess.Popen(
            cmd,
            #creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == 'win32' else 0
        )
        processes.append(p)

    print("\n✅ Network is running! (Press Ctrl+C in this terminal to stop all nodes)")
    
    try:
        # 保持主脚本运行，直到用户按 Ctrl+C 
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down network...")
        for p in processes:
            p.terminate()

if __name__ == "__main__":
    start_network()