import sys
import os
import copy
#找到某级为infrastructure的父目录,插入搜索列表最前面
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = current_dir
while not os.path.exists(os.path.join(project_root, "infrastructure")):
    parent = os.path.dirname(project_root)
    if parent == project_root: break #到了文件系统根目录了
    project_root = parent
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import time
import json
from web3 import Web3
from infrastructure.utils import REGISTRY_ADDRESS, REGISTRY_ABI

# === 1. 实验参数与配置 ===
AGENT_NAMES = ["agent_a", "agent_b", "agent_c", "agent_d"]
FUND_AMOUNT = 0.005                 # 给每个 Admin 转账的金额 (ETH)
FUNDER_ACCOUNT_KEY = "master"       # key.json 中用于出资的主账户

# --- 输出文件路径 ---
# 脚本在 _demo_2v2 文件夹，输出需在 ../config 文件夹
CONFIG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'config'))
KEY_OUTPUT_FILE = os.path.join(CONFIG_DIR, "agents_4_key.json")

# 1. 读取 config/key.json (获取 Master 资金和 Issuer 身份)
source_key_file = os.path.join(CONFIG_DIR, 'key.json')
with open(source_key_file, 'r', encoding='utf-8') as f:
    config = json.load(f)

# 2. 手动建立 Web3 连接 (跳过 get_w3 函数以避免路径错误)
# 优先使用 key.json 中的 url，如果没有则使用默认值
node_url = config.get("api_url", "https://ethereum-sepolia.publicnode.com")
w3 = Web3(Web3.HTTPProvider(node_url))

def generate_accounts(names):
    """生成指定命名的密钥对 (Admin + Op)"""
    print(f"\n[Step 1] 正在生成 {len(names)} 组 Agent 账户...")
    agents = []
    for name in names:
        # 使用 extra_entropy (附加随机熵)增加随机性
        admin_acct = w3.eth.account.create(extra_entropy=f"{name}_admin_{time.time()}")
        op_acct = w3.eth.account.create(extra_entropy=f"{name}_op_{time.time()}")
        
        agents.append({
            "name": name, # e.g., agent_a
            "admin": {"address": admin_acct.address, "private_key": admin_acct.key.hex()},
            "op": {"address": op_acct.address, "private_key": op_acct.key.hex()}
        })
        print(f"    生成: {name}_admin / {name}_op")
    print(f"    生成完成。")
    return agents

def fund_accounts(agents, funder_info):
    """主账户批量向 Agent Admin 账户转账"""
    funder_addr = funder_info["address"]
    funder_pk = funder_info["private_key"]
    
    print(f"\n[Step 2] 主账户 {funder_addr} 正在分发 ETH...")
    
    # 获取主账户的下一笔可用的 nonce
    start_nonce = w3.eth.get_transaction_count(funder_addr, 'pending')
    
    tx_hashes = []
    
    for i, agent in enumerate(agents):
        target_address = agent["admin"]["address"]
        
        tx = {
            'nonce': start_nonce + i, # 关键：手动递增 Nonce 实现并发广播
            'to': target_address,
            'value': w3.to_wei(FUND_AMOUNT, 'ether'),
            'gas': 21000,
            'gasPrice': int(w3.eth.gas_price * 1.2),
            'chainId': 11155111
        }
        
        signed_tx = w3.eth.account.sign_transaction(tx, funder_pk)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)#把原始交易发送给当前连接的节点,节点广播到整个网络
        tx_hashes.append(tx_hash)
        print(f"    -> 转账给 {agent['name']} Admin,交易哈希: {w3.to_hex(tx_hash)}")
    
    print("    等待转账确认...")
    for tx_hash in tx_hashes:
        w3.eth.wait_for_transaction_receipt(tx_hash)
    print("    所有账户资金到账！")

def register_dids(agents):
    """通过给自己转账 0 ETH 进行隐式注册 DID (并行发送，统一等待)"""
    print(f"\n[Step 3] Agents 正在通过隐式方式注册 DID (自转账 0 ETH)...")
    
    # 获取当前 gas price 并提升 50% 以确保交易被优先处理
    current_gas_price = int(w3.eth.gas_price * 1.5)
    print(f"    当前 Gas Price: {w3.eth.gas_price}, 使用: {current_gas_price}")
    
    # 第一阶段：并行发送所有交易
    pending_txs = []
    for agent in agents:
        admin_addr, admin_pk = agent["admin"]["address"], agent["admin"]["private_key"]
        
        try:
            nonce = w3.eth.get_transaction_count(admin_addr, 'pending')
            
            # === 构建 0 ETH 自转账交易 ===
            tx = {
                'nonce': nonce,
                'to': admin_addr,        # 发给自己
                'value': 0,              # 金额 0
                'gas': 21000,            # 简单转账只需 21000
                'gasPrice': current_gas_price,
                'chainId': 11155111
            }
            
            signed_tx = w3.eth.account.sign_transaction(tx, admin_pk)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            pending_txs.append((agent['name'], tx_hash))
            print(f"    {agent['name']} 交易已广播: {w3.to_hex(tx_hash)}")

        except Exception as e:
            print(f"    {agent['name']} 广播失败: {e}")
    
    # 第二阶段：统一等待所有交易确认
    print("    等待所有交易确认...")
    for name, tx_hash in pending_txs:
        try:
            w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
            print(f"    {name} 隐式注册成功")
        except Exception as e:
            print(f"    {name} 注册失败: {e}")
            
    print("    DID 注册完成。")

def add_delegates(agents):
    """
    添加 Delegate (并行发送，统一等待)
    给 admin_addr 这个 DID，写入一条属性：
“它的 Secp256k1 签名认证公钥 = op_addr”，有效期一年
    """
    print(f"\n[Step 4] 正在添加 Delegate (Op Key)...")
    contract = w3.eth.contract(address=REGISTRY_ADDRESS, abi=REGISTRY_ABI)
    validity = 365 * 24 * 60 * 60#一年有效期
    key_name_bytes = "did/pub/Secp256k1/sigAuth/hex".encode('utf-8').ljust(32, b'\0')#可用于签名认证的公钥
    
    # 获取当前 gas price 并提升 50% 以确保交易被优先处理
    current_gas_price = int(w3.eth.gas_price * 1.5)
    print(f"    当前 Gas Price: {w3.eth.gas_price}, 使用: {current_gas_price}")

    # 第一阶段：并行发送所有交易
    pending_txs = []
    for agent in agents:
        admin_addr, admin_pk = agent["admin"]["address"], agent["admin"]["private_key"]
        op_addr = agent["op"]["address"]
        value_bytes = bytes.fromhex(op_addr[2:])

        try:
            nonce = w3.eth.get_transaction_count(admin_addr, 'pending')
            tx_func = contract.functions.setAttribute(admin_addr, key_name_bytes, value_bytes, validity)
            tx = tx_func.build_transaction({
                'chainId': 11155111, 'gas': 200000,
                'gasPrice': current_gas_price, 'nonce': nonce
            })
            
            signed_tx = w3.eth.account.sign_transaction(tx, admin_pk)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            pending_txs.append((agent['name'], tx_hash))
            print(f"    {agent['name']} 交易已广播: {w3.to_hex(tx_hash)}")

        except Exception as e:
            print(f"    {agent['name']} 广播失败: {e}")

    # 第二阶段：统一等待所有交易确认
    print("    等待所有交易确认...")
    for name, tx_hash in pending_txs:
        try:
            w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
            print(f"    {name} 授权 OP 成功")
        except Exception as e:
            print(f"    {name} 授权失败: {e}")

    print("    Delegate 添加完成。")

def save_keys_to_file(agents):
    """
    保存密钥信息到 config/agents_4_key.json
    """
    print(f"\n[Step 5] 保存账户密钥到 {KEY_OUTPUT_FILE} ...")
    
    # 1. 直接从 key.json 复制所有配置，只重置 accounts
    output_data = {**config, "accounts": {}}
    # 从原配置文件(key.json)中读取 issuer 信息
    # 注意：config 是脚本开头通过 get_w3() 加载的全局变量
    if "issuer" in config["accounts"]:
        output_data["accounts"]["issuer"] = config["accounts"]["issuer"]
    else:
        print("    [警告] 在 key.json 中未找到 'issuer' 账户信息")

    # 2. 将 agents 列表按照顺序写入 accounts 字典
    for agent in agents:
        name = agent['name']
        
        # 添加 Admin 账户: agent_x_admin
        output_data["accounts"][f"{name}_admin"] = copy.deepcopy(agent["admin"])
        
        # 添加 Op 账户: agent_x_op
        output_data["accounts"][f"{name}_op"] = copy.deepcopy(agent["op"])

    # 3. 确保目标目录存在
    os.makedirs(os.path.dirname(KEY_OUTPUT_FILE), exist_ok=True)

    # 4. 写入文件
    with open(KEY_OUTPUT_FILE, "w", encoding='utf-8') as f:
        json.dump(output_data, f, indent=4, ensure_ascii=False)
    print(f"    保存成功！")

def main():
    funder_info = config["accounts"].get(FUNDER_ACCOUNT_KEY)#master账户

    if not funder_info:
        print(f"错误: key.json 中找不到主账户 '{FUNDER_ACCOUNT_KEY}'")
        return
    
    try:
        # 生成
        agents = generate_accounts(AGENT_NAMES)
        
        # 转账
        fund_accounts(agents, funder_info)
        time.sleep(2)
        
        # 注册
        register_dids(agents)
        time.sleep(2)
        
        # 授权
        add_delegates(agents)
        
        # 保存
        save_keys_to_file(agents)
        
        print("\n=== 所有操作执行完毕 ===")
        
    except Exception as e:
        print(f"\n[Error] 脚本执行过程中发生错误: {e}")
        if 'agents' in locals():
            print("尝试保存已生成的账户信息...")
            save_keys_to_file(agents)

if __name__ == "__main__":
    main()