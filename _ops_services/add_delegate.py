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

from infrastructure.utils import get_w3, REGISTRY_ADDRESS, REGISTRY_ABI

def add_delegate_with_contract(admin_role: str, op_role: str):
    """
    通过调用 ethr-did-registry 合约的 setAttribute 函数，
    将一个 Operator 角色授权为 Admin 角色的 Delegate。
    
    Args:
        admin_role (str): 授权方 (Owner) 的角色名, e.g., 'agent_a_admin'
        op_role (str):    被授权方 (Delegate) 的角色名, e.g., 'agent_a_op'
    """
    print("="*50)
    print(f"===  添加 Delegate 授权 ===")
    print(f"  Owner:    {admin_role}")
    print(f"  Delegate: {op_role}")
    print("="*50)

    try:
        # 1. 初始化 Web3 连接和配置
        w3, config = get_w3()
        accounts = config.get("accounts", {})

        # 2. 检查并获取账户信息
        if admin_role not in accounts or op_role not in accounts:
            print(f"[错误] 角色 '{admin_role}' 或 '{op_role}' 在 key.json 中未找到。")
            return

        admin_info = accounts[admin_role]
        op_addr = accounts[op_role]["address"]
        
        admin_addr = admin_info["address"]
        admin_pk = admin_info["private_key"]
        
        print(f"[*] Owner (Admin) Address: {admin_addr}")
        print(f"[*] Delegate (Op) Address:  {op_addr}")

        # 3. 获取合约实例
        registry_contract = w3.eth.contract(address=REGISTRY_ADDRESS, abi=REGISTRY_ABI)

        # 4. 构造合约调用参数
        key_name_str = "did/pub/Secp256k1/sigAuth/hex"
        key_name_bytes = key_name_str.encode('utf-8').ljust(32, b'\0')
        value_bytes = bytes.fromhex(op_addr[2:])
        validity = 365 * 24 * 60 * 60  # 有效期1年

        # 5. 构建交易
        nonce = w3.eth.get_transaction_count(admin_addr)
        
        transaction = registry_contract.functions.setAttribute(
            identity=admin_addr,    # 对 Admin 自己的身份进行设置
            name=key_name_bytes,    # 属性名 (授权类型)
            value=value_bytes,      # 属性值 (Op 地址)
            validity=validity
        ).build_transaction({
            'from': admin_addr,
            'nonce': nonce,
            'gas': 200000,
            'gasPrice': w3.eth.gas_price*2,
            'chainId': 11155111 # Sepolia
        })
        
        # 6. 使用 Admin 私钥签名并发送
        signed_tx = w3.eth.account.sign_transaction(transaction, admin_pk)
        
        print("\n[*] 正在发送交易至 Sepolia 网络...")
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        print(f"[*] 交易已广播, Hash: {w3.to_hex(tx_hash)}")
        print("[*] 等待交易确认...")

        # 7. 等待交易回执
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        
        # 8. 确认结果
        if receipt.status == 1:
            print("\n" + "-"*50)
            print(f" 成功! '{op_role}' 已被授权为 '{admin_role}' 的 Delegate。")
            print(f"   区块号: {receipt.blockNumber}")
            print(f"   Gas消耗: {receipt.gasUsed}")
            print("-" * 50)
        else:
            print(f"\n[失败] 交易执行失败，请检查区块浏览器获取详情: {w3.to_hex(tx_hash)}")

    except Exception as e:
        print(f"\n[严重错误] 脚本执行异常: {e}")

if __name__ == "__main__":
    # 从命令行接收参数，例如: python other_entities/add_delegate.py agent_a_admin agent_a_op 
    if len(sys.argv) < 3:
        print("用法: python other_entities/add_delegate.py <admin_role> <op_role>")
        print("例如: python other_entities/add_delegate.py agent_a_admin agent_a_op")
        sys.exit(1)
    
    admin_role_arg = sys.argv[1]
    op_role_arg = sys.argv[2]
    
    add_delegate_with_contract(admin_role_arg, op_role_arg)