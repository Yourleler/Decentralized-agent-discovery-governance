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
import datetime
from web3 import Web3
from eth_account.messages import encode_defunct

# 引入项目组件
try:
    from infrastructure.load_config import load_key_config
except ImportError:
    print("❌ 错误: 无法导入 infrastructure。")
    sys.exit(1)

#  初始化资源 
print("正在加载配置...")
try:
    config = load_key_config()
    accounts = config["accounts"]
    issuer_info = accounts["issuer"]
    w3 = Web3()
except Exception as e:
    print(f"❌ 配置加载失败: {e}")
    print("请检查 config/key.json 是否存在且格式正确。")
    sys.exit(1)

# 模板目录
SCHEMA_DIR = os.path.join(project_root, "vc_schemas")

# 核心逻辑

def sign_vc(vc_payload, private_key):
    """
    对 JSON 进行排序并签名
    """
    serialized_data = json.dumps(vc_payload, sort_keys=True, separators=(',', ':'))
    message = encode_defunct(text=serialized_data)
    signed_message = w3.eth.account.sign_message(message, private_key=private_key)
    return signed_message.signature.hex()

def get_iso_time(offset_days=0):
    dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=offset_days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

def process_single_template(template_data, applicant_did):
    """
    处理单个模板数据：替换 ID -> 补充信息 -> 签名
    """
    # 深拷贝
    vc_payload = json.loads(json.dumps(template_data))

    # 1. 替换 ID
    if "credentialSubject" in vc_payload:
        vc_payload["credentialSubject"]["id"] = applicant_did
    else:
        vc_payload["credentialSubject"] = {"id": applicant_did}

    # 2. 补全 Issuer 和 时间信息
    # 注意：这里 hardcode 了 sepolia 格式，保持和 server 一致
    issuer_did = f"did:ethr:sepolia:{issuer_info['address']}"
    vc_payload["issuer"] = issuer_did
    
    if "validFrom" not in vc_payload:
        vc_payload["validFrom"] = get_iso_time(0)
    if "validUntil" not in vc_payload:
        vc_payload["validUntil"] = get_iso_time(365)

    # 3. 签名
    signature = sign_vc(vc_payload, issuer_info["private_key"])

    # 4. 包装 Proof
    final_vc = vc_payload.copy()
    final_vc["proof"] = {
        "type": "EcdsaSecp256k1Signature2019",
        "created": get_iso_time(0),
        "proofPurpose": "assertionMethod",
        "verificationMethod": f"{issuer_did}#controller",
        "jws": signature
    }
    
    return final_vc

# 实验主程序

def run_measurement():
    print("="*60)
    print("VC Size Measurement Experiment (Based on Actual Issuer Logic)")
    print(f"Template Directory: {SCHEMA_DIR}")
    print(f"Issuer DID: did:ethr:sepolia:{issuer_info['address']}")
    print("="*60)

    if not os.path.exists(SCHEMA_DIR):
        print(f"❌ 错误: 找不到模板目录 {SCHEMA_DIR}")
        return

    files = sorted([f for f in os.listdir(SCHEMA_DIR) if f.endswith(".json")])
    if not files:
        print("❌ 错误: 目录中没有 JSON 文件")
        return
    
    # 测试申请人 DID (使用配置中的 agent_a_admin 作为示例)
    if "agent_a_admin" in accounts:
        applicant_address = accounts["agent_a_admin"]["address"]
        test_applicant_did = f"did:ethr:sepolia:{applicant_address}"
        print(f"Test Applicant (agent_a_admin) DID: {test_applicant_did}")
    else:
        # 如果 key.json 里没配这个角色，作为后备方案随机生成
        print("⚠️ Warning: agent_a_admin not found in key.json, using random address.")
        dummy_account = w3.eth.account.create()
        test_applicant_did = f"did:ethr:sepolia:{dummy_account.address}"
    print(f"Test Applicant DID: {test_applicant_did}")
    print("-" * 80)
    print(f"{'Filename':<35} | {'Type':<30} | {'Size (Bytes)':<10}")
    print("-" * 80)

    total_size = 0
    
    for filename in files:
        file_path = os.path.join(SCHEMA_DIR, filename)
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                template = json.load(f)

            # --- 核心步骤：调用真实签发逻辑 ---
            vc = process_single_template(template, test_applicant_did)

            # --- 测量大小 ---
            vc_json_str = json.dumps(vc, separators=(',', ':'))
            vc_size_bytes = len(vc_json_str.encode('utf-8'))
            vc_size_kb = vc_size_bytes / 1024  # KB
            
            # 获取 VC 类型名称用于展示
            vc_type_list = template.get("type", ["Unknown"])
            vc_type_name = vc_type_list[-1] if isinstance(vc_type_list, list) else str(vc_type_list)

            # 修改打印格式，保留2位小数
            print(f"{filename:<35} | {vc_type_name:<30} | {vc_size_kb:<10.2f} KB")
            total_size += vc_size_bytes

        except Exception as e:
            print(f"Error processing {filename}: {e}")

    print("-" * 80)
    avg_size_kb = (total_size / len(files)) / 1024
    print(f"Average VC Size: {avg_size_kb:.2f} KB")
    print("="*60)
    print("测量完成。")

if __name__ == "__main__":
    run_measurement()