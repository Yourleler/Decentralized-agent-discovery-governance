import sys
import os
import time
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = current_dir
while not os.path.exists(os.path.join(project_root, "infrastructure")):
    parent = os.path.dirname(project_root)
    if parent == project_root: break 
    project_root = parent
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from infrastructure.utils import get_w3

def register_did_implicit(role_name: str):
    """
    通过向自己发送 0 ETH 的交易来完成隐式 DID 注册。
    
    Args:
        role_name (str): 在 key.json 中定义的目标角色名。
    """
    print("="*50)
    print(f"===  隐式注册 DID (自转账 0 ETH): {role_name} ===")
    print("="*50)

    try:
        # 1. 初始化 Web3 连接和配置
        w3, config = get_w3()
        accounts = config.get("accounts", {})

        # 2. 检查并获取账户信息
        if role_name not in accounts:
            print(f"[错误] 角色 '{role_name}' 在 key.json 中未找到。")
            return

        account_info = accounts[role_name]
        address = account_info["address"]
        private_key = account_info["private_key"]
        print(f"[*] 目标地址: {address}")

        # 3. 检查余额
        balance = w3.eth.get_balance(address)
        if balance == 0:
            print(f"[错误] 账户余额为 0，无法支付 Gas 费用，请先充值 Sepolia 测试币。")
            return

        # 4. 构造隐式注册交易
        # 逻辑：Nonce自增，To地址为自己，Value为0
        nonce = w3.eth.get_transaction_count(address, 'pending')
        
        tx = {
            'nonce': nonce,
            'to': address,           # 发给自己
            'value': 0,              # 金额 0
            'gas': 21000,            # 安全 Gas Limit 
            'gasPrice': w3.eth.gas_price*2,
            'chainId': 11155111      # Sepolia Chain ID
        }
        
        # 5. 签名并发送交易
        signed_tx = w3.eth.account.sign_transaction(tx, private_key)
        
        print("\n[*] 正在发送隐式注册交易至 Sepolia 网络...")
        start_time = time.time()
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        print(f"[*] 交易已广播, Hash: {w3.to_hex(tx_hash)}")
        print("[*] 等待交易确认...")

        # 6. 等待交易回执
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        end_time = time.time()
        
        # 7. 确认结果
        if receipt.status == 1:
            latency = end_time - start_time
            print("\n" + "-"*50)
            print(f" 成功! DID '{role_name}' 已通过隐式方式注册。")
            print(f"   区块号: {receipt.blockNumber}")
            print(f"   Gas消耗: {receipt.gasUsed}")
            print(f"   耗时: {latency:.2f}s")
            print("-" * 50)
        else:
            print(f"\n[失败] 交易执行失败，请检查区块浏览器获取详情: {w3.to_hex(tx_hash)}")

    except Exception as e:
        print(f"\n[严重错误] 脚本执行异常: {e}")

if __name__ == "__main__":
    # 从命令行接收参数，例如: python other_entities/register_did.py agent_a_admin 
    if len(sys.argv) < 2:
        print("用法: python other_entities/register_did.py <角色名>")
        print("例如: python other_entities/register_did.py agent_a_admin")
        sys.exit(1)
    
    target_role = sys.argv[1]
    register_did_implicit(target_role)