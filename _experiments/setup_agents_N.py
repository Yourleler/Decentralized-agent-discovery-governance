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
# 创建 data 目录（如果不存在）
DATA_DIR = os.path.join(project_root, "data")
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
import time
import json
import csv

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from infrastructure.utils import get_w3, REGISTRY_ADDRESS, REGISTRY_ABI

# === 1. 实验参数与配置 ===
# --- 可调参数 ---
NUM_VERIFIERS = 1                   # 生成账户数量
FUND_AMOUNT = 0.005                 # 给每个 Admin 转账的金额 (ETH)
FUNDER_ACCOUNT_KEY = "master"       # key.json 中用于出资的主账户
# --- 输出文件 ---
KEY_OUTPUT_FILE = os.path.join(DATA_DIR, "verifiers_key.json")
CSV_REPORT_FILE = os.path.join(DATA_DIR, "setup_verifiers.csv")
# --- 成本估算常量 ---
FIXED_MAINNET_GAS_GWEI = 4.88       # 年度均值 Gwei
ETH_PRICE_USD = 3121.34             # 年度均值 USD

# 初始化连接 (加载原始配置)
w3, config = get_w3()


def generate_accounts(num):
    """生成指定数量的密钥对 (Admin + Op)"""
    print(f"\n[Step 1] 正在生成 {num} 组 Verifier 账户...")
    verifiers = []
    for i in range(1, num + 1):
        # 使用 extra_entropy 增加随机性
        admin_acct = w3.eth.account.create(extra_entropy=f"admin_{i}_{time.time()}")
        op_acct = w3.eth.account.create(extra_entropy=f"op_{i}_{time.time()}")
        
        verifiers.append({
            "id": i, 
            "name": f"verifier_{i}",
            "admin": {"address": admin_acct.address, "private_key": admin_acct.key.hex()},
            "op": {"address": op_acct.address, "private_key": op_acct.key.hex()}
        })
    print(f"    生成完成。")
    return verifiers

def fund_accounts(verifiers, funder_info):
    """主账户批量向新账户转账"""
    funder_addr = funder_info["address"]
    funder_pk = funder_info["private_key"]
    
    print(f"\n[Step 2] 主账户 {funder_addr} 正在分发 ETH...")
    
    # 获取主账户的初始 Nonce
    start_nonce = w3.eth.get_transaction_count(funder_addr, 'pending')
    
    tx_hashes = []
    
    for i, v in enumerate(verifiers):
        target_address = v["admin"]["address"]
        
        tx = {
            'nonce': start_nonce + i, # 关键：手动递增 Nonce 实现并发广播
            'to': target_address,
            'value': w3.to_wei(FUND_AMOUNT, 'ether'),
            'gas': 21000,
            'gasPrice': int(w3.eth.gas_price * 1.2),
            'chainId': 11155111
        }
        
        signed_tx = w3.eth.account.sign_transaction(tx, funder_pk)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        tx_hashes.append(tx_hash)
        print(f"    -> 转账给 Verifier {v['id']} Admin: {w3.to_hex(tx_hash)}")
    
    print("    等待转账确认...")
    for tx_hash in tx_hashes:
        w3.eth.wait_for_transaction_receipt(tx_hash)
    print("    所有账户资金到账！")

def register_dids_and_measure(verifiers):
    """通过给自己转账0ETH进行隐式注册DID，并测量性能指标。"""
    print(f"\n[Step 3] Verifiers 正在进行隐式注册 (给自己转账 0 ETH)...")
    results = []
    
    # 隐式注册不需要合约实例

    for v in verifiers:
        admin_addr, admin_pk = v["admin"]["address"], v["admin"]["private_key"]
        
        try:
            nonce = w3.eth.get_transaction_count(admin_addr, 'pending')
            
            tx = {
                'nonce': nonce,
                'to': admin_addr,        # 发给自己
                'value': 0,              # 金额 0
                'gas': 100000,           # Gas Limit
                'gasPrice': w3.eth.gas_price,
                'chainId': 11155111
            }
            
            signed_tx = w3.eth.account.sign_transaction(tx, admin_pk)
            
            start_time = time.time()
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
            end_time = time.time()
            
            # 计算指标
            latency = end_time - start_time
            gas_used = receipt['gasUsed']
            cost_eth = gas_used * FIXED_MAINNET_GAS_GWEI * (10**-9)
            cost_usd = cost_eth * ETH_PRICE_USD
            
            results.append({
                "id": v["id"],
                "latency": latency, "gas_used": gas_used, "cost_usd": cost_usd
            })
            print(f"    Verifier {v['id']} 隐式注册成功 (耗时: {latency:.2f}s, Gas: {gas_used})")

        except Exception as e:
            print(f"    Verifier {v['id']} 注册失败: {e}")
            results.append({"id": v["id"], "latency": -1, "gas_used": -1, "cost_usd": -1})
            
    print("    隐式 DID 注册完成。")
    return results

def add_delegates_and_measure(verifiers):
    """添加 Delegate 并测量性能指标。"""
    print(f"\n[Step 4] 正在添加 Delegate 并进行测量...")
    results = []
    contract = w3.eth.contract(address=REGISTRY_ADDRESS, abi=REGISTRY_ABI)
    validity = 365 * 24 * 60 * 60
    key_name_bytes = "did/pub/Secp256k1/sigAuth/hex".encode('utf-8').ljust(32, b'\0')

    for v in verifiers:
        admin_addr, admin_pk = v["admin"]["address"], v["admin"]["private_key"]
        op_addr = v["op"]["address"]
        value_bytes = bytes.fromhex(op_addr[2:])

        try:
            nonce = w3.eth.get_transaction_count(admin_addr, 'pending')
            tx_func = contract.functions.setAttribute(admin_addr, key_name_bytes, value_bytes, validity)
            tx = tx_func.build_transaction({
                'chainId': 11155111, 'gas': 200000,
                'gasPrice': w3.eth.gas_price, 'nonce': nonce
            })
            
            signed_tx = w3.eth.account.sign_transaction(tx, admin_pk)

            start_time = time.time()
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
            end_time = time.time()
            
            # 计算指标
            latency = end_time - start_time
            gas_used = receipt['gasUsed']
            cost_eth = gas_used * FIXED_MAINNET_GAS_GWEI * (10**-9)
            cost_usd = cost_eth * ETH_PRICE_USD

            results.append({
                "id": v["id"],
                "latency": latency, "gas_used": gas_used, "cost_usd": cost_usd
            })
            print(f"    Verifier {v['id']} 授权成功 (耗时: {latency:.2f}s, Gas: {gas_used})")

        except Exception as e:
            print(f"    Verifier {v['id']} 授权失败: {e}")
            results.append({"id": v["id"], "latency": -1, "gas_used": -1, "cost_usd": -1})

    print("    Delegate 添加完成。")
    return results

def generate_report(reg_results, del_results):
    """生成 CSV 报告并打印摘要"""
    print(f"\n[Step 5] 正在生成实验报告...")

    # A. 合并数据
    merged_data = []
    del_map = {res["id"]: res for res in del_results}
    for reg_res in reg_results:
        verifier_id = reg_res["id"]
        del_res = del_map.get(verifier_id, {})
        merged_data.append({
            "Verifier ID": verifier_id,
            "Register Latency (s)": reg_res.get("latency", -1),
            "Register Gas Used": reg_res.get("gas_used", -1),
            "Register Cost (USD)": reg_res.get("cost_usd", -1),
            "Delegate Latency (s)": del_res.get("latency", -1),
            "Delegate Gas Used": del_res.get("gas_used", -1),
            "Delegate Cost (USD)": del_res.get("cost_usd", -1),
        })

    # B. 写入 CSV
    try:
        with open(CSV_REPORT_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=merged_data[0].keys())
            writer.writeheader()
            writer.writerows(merged_data)
        print(f"    详细数据已保存到: {CSV_REPORT_FILE}")
    except Exception as e:
        print(f"    [Error] 保存 CSV 失败: {e}")

    # C. 计算并打印平均值
    print("\n" + "="*80)
    print("=== 实验结果平均值摘要 ===")
    
    valid_reg_results = [r for r in reg_results if r['latency'] != -1]
    valid_del_results = [r for r in del_results if r['latency'] != -1]
    
    if not valid_reg_results or not valid_del_results:
        print("有效数据不足，无法计算平均值。")
        return
        
    avg_reg_latency = sum(r['latency'] for r in valid_reg_results) / len(valid_reg_results)
    avg_reg_gas = sum(r['gas_used'] for r in valid_reg_results) / len(valid_reg_results)
    avg_reg_cost = sum(r['cost_usd'] for r in valid_reg_results) / len(valid_reg_results)
    
    avg_del_latency = sum(r['latency'] for r in valid_del_results) / len(valid_del_results)
    avg_del_gas = sum(r['gas_used'] for r in valid_del_results) / len(valid_del_results)
    avg_del_cost = sum(r['cost_usd'] for r in valid_del_results) / len(valid_del_results)

    print(f"{'Metric':<25} | {'Register DID':<25} | {'Add Delegate'}")
    print("-" * 80)
    print(f"{'Avg. Latency (s)':<25} | {avg_reg_latency:<25.4f} | {avg_del_latency:.4f}")
    print(f"{'Avg. Gas Used':<25} | {avg_reg_gas:<25.0f} | {avg_del_gas:.0f}")
    print(f"{'Avg. Est. Cost (USD)':<25} | ${avg_reg_cost:<24.4f} | ${avg_del_cost:.4f}")
    print("="*80)

def save_keys_to_file(verifiers):
    """
    保存密钥信息到文件，格式完全兼容 key.json 的结构
    (包含 api_url, qwq_api_key, accounts)
    """
    print(f"\n[Step 6] 保存账户密钥到 {KEY_OUTPUT_FILE} ...")
    
    # 1. 继承原有的全局配置
    output_data = {
        "api_url": config.get("api_url", "https://ethereum-sepolia.publicnode.com"),
        "qwq_api_key": config.get("qwq_api_key", ""),
        "accounts": {}
    }

    # 2. 将 verifiers 列表展平为 accounts 字典
    for v in verifiers:
        # e.g. verifier_1
        base_name = f"verifier_{v['id']}"
        
        # 添加 Admin 账户
        # key: verifier_1_admin
        output_data["accounts"][f"{base_name}_admin"] = {
            "address": v["admin"]["address"],
            "private_key": v["admin"]["private_key"]
        }
        
        # 添加 Op 账户
        # key: verifier_1_op
        output_data["accounts"][f"{base_name}_op"] = {
            "address": v["op"]["address"],
            "private_key": v["op"]["private_key"]
        }

    # 3. 写入文件
    with open(KEY_OUTPUT_FILE, "w", encoding='utf-8') as f:
        json.dump(output_data, f, indent=4, ensure_ascii=False)
    print(f"    保存成功！文件格式已适配 key.json 标准。")

def main():
    funder_info = config["accounts"].get(FUNDER_ACCOUNT_KEY)
    if not funder_info:
        print(f"错误: key.json 中找不到主账户 '{FUNDER_ACCOUNT_KEY}'")
        return
    
    try:
        verifiers = generate_accounts(NUM_VERIFIERS)
        fund_accounts(verifiers, funder_info)
        time.sleep(3)
        
        registration_results = register_dids_and_measure(verifiers)
        time.sleep(3)
        delegation_results = add_delegates_and_measure(verifiers)

        generate_report(registration_results, delegation_results)

        # 保存格式化后的密钥
        save_keys_to_file(verifiers)
        
        print("\n=== 所有操作执行完毕 ===")
        
    except Exception as e:
        print(f"\n[Error] 脚本执行过程中发生错误: {e}")
        if 'verifiers' in locals():
            print("尝试保存已生成的账户信息...")
            save_keys_to_file(verifiers)

if __name__ == "__main__":
    main()
