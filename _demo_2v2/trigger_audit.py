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
import requests
import threading

from infrastructure.wallet import IdentityWallet

CONFIG_PATH = os.path.join(project_root, "config", "network_config.json")

def get_did_by_role(role_name):
    """
    利用本地 Key 直接计算出指定角色的 DID，
    这样就不需要手动复制粘贴了。
    """
    try:
        wallet = IdentityWallet(role_name)
        return wallet.did
    except Exception as e:
        print(f"[Warning] 无法计算角色 {role_name} 的 DID: {e}")
        return None

def trigger_single_audit(verifier_name, verifier_port, target_holder_name, target_did):
    """
    单个审计任务的执行函数
    """
    url = f"http://localhost:{verifier_port}/control/start_audit"
    payload = {
        "target_holder_did": target_did
    }
    
    print(f"{verifier_name} (Port {verifier_port}) ->  {target_holder_name} ({target_did[:8]}...)")
    
    try:
        response = requests.post(url, json=payload, timeout=120)
        
        if response.status_code == 200:
            res_json = response.json()
            status = res_json.get("status", "Unknown")
            print(f"✅ [{verifier_name}] 响应: {status}")
        else:
            print(f"❌ [{verifier_name}] HTTP Error {response.status_code}: {response.text}")
            
    except Exception as e:
        print(f"❌ [{verifier_name}] 请求失败: {e}")

def main():
    print("="*60)
    print(" 2v2 演示场景：自动触发多角色验证流程")
    print("="*60)

    # 1. 读取配置文件
    if not os.path.exists(CONFIG_PATH):
        print(f"[Error] 找不到配置文件: {CONFIG_PATH}")
        return

    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = json.load(f)

    # 2. 预计算 Holder 的信息映射表
    # 结构: { "http://localhost:5000": {"did": "did:ethr:...", "name": "Holder-A"} }
    holder_map = {}
    
    print("[1/3] 正在解析 Holder 身份...")
    for h in config["holders"]:
        port = h["port"]
        role = h["role"]
        name = h["name"]
        
        # 自动计算 DID
        did = get_did_by_role(role)
        if not did:
            print(f"[Fatal] 无法获取 {name} 的 DID，终止。")
            return
            
        # 建立映射键：目标 URL (Verifier config 里配的是 http://localhost:PORT)
        key_url = f"http://localhost:{port}"
        holder_map[key_url] = {
            "did": did,
            "name": name
        }
        print(f"   -> {name} ({port}) = {did}")

    # 3. 准备并发任务
    print("\n[2/3] 正在匹配 Verifier 目标...")
    threads = []
    
    for v in config["verifiers"]:
        v_name = v["name"]
        v_port = v["port"]
        target_url = v["target_url"] # 例如 http://localhost:5000
        
        # 自动匹配
        target_info = holder_map.get(target_url)
        
        if not target_info:
            print(f"   ⚠️  跳过 {v_name}: 它的目标 {target_url} 在 holders 配置中找不到对应的端口。")
            continue
            
        target_did = target_info["did"]
        target_holder_name = target_info["name"]
        
        # 创建线程 
        t = threading.Thread(
            target=trigger_single_audit,
            args=(v_name, v_port, target_holder_name, target_did)
        )
        threads.append(t)

    # 4. 并发执行
    if not threads:
        print("[Error] 没有可执行的任务。")
        return

    print(f"\n[3/3] 同时触发 {len(threads)} 个验证流程...\n")
    
    # 启动所有线程
    for t in threads:
        t.start()
        
    # 等待所有线程结束
    for t in threads:
        t.join()
        
    print("\n" + "="*60)
    print("所有点火指令已发送完毕。请检查各个终端窗口查看详细日志。")

if __name__ == "__main__":
    main()