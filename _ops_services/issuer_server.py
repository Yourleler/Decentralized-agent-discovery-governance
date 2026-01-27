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
import time
import datetime
import traceback
from flask import Flask, request, jsonify
from web3 import Web3
from eth_account.messages import encode_defunct

# === 1. 引入项目组件 ===
from infrastructure.load_config import load_key_config
from infrastructure.validator import DIDValidator

app = Flask(__name__)

# === 2. 初始化配置 ===
config = load_key_config() 
accounts = config["accounts"]
issuer_info = accounts["issuer"]
w3 = Web3()
validator = DIDValidator()

# 模板目录
SCHEMA_DIR = os.path.join(project_root, "vc_schemas")

print("="*60)
print(f"Issuer Server Started (Port: 8000)")
print(f"Issuer DID: did:ethr:sepolia:{issuer_info['address']}")
print(f"Template Dir: {SCHEMA_DIR}")
print("="*60)

# === 3. 核心工具函数 ===

def sign_vc(vc_payload, private_key):
    """对 JSON 进行排序并签名"""
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

    vc_payload = json.loads(json.dumps(template_data))

    # 1. 替换 ID 
    if "credentialSubject" in vc_payload:
        vc_payload["credentialSubject"]["id"] = applicant_did
    else:
        vc_payload["credentialSubject"] = {"id": applicant_did}

    # 2. 补全 Issuer 和 时间信息
    issuer_did = f"did:ethr:sepolia:{issuer_info['address']}"
    vc_payload["issuer"] = issuer_did
    
    # 如果模板里没有有效期，则自动生成
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

def generate_all_vcs(applicant_did):
    """
    遍历 vc_schemas 目录，为 applicant 签发所有模板定义的 VCs
    """
    issued_vcs = []
    
    if not os.path.exists(SCHEMA_DIR):
        print(f"[Error] Schema dir not found: {SCHEMA_DIR}")
        return []

    # 获取所有 json 文件并排序
    files = sorted([f for f in os.listdir(SCHEMA_DIR) if f.endswith(".json")])
    
    print(f"    [Process] Found {len(files)} templates. Processing...")

    for filename in files:
        file_path = os.path.join(SCHEMA_DIR, filename)
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                template = json.load(f)
            
            # 处理单个模板
            vc = process_single_template(template, applicant_did)
            issued_vcs.append(vc)

            vc_json_str = json.dumps(vc)
            vc_size_bytes = len(vc_json_str)
            vc_size_kb = vc_size_bytes / 1024
            
            vc_type = template.get("type", ["Unknown"])[-1]
            #print(f"      - Issued: {filename} -> {vc_type} | Size: {vc_size_bytes} bytes ({vc_size_kb:.2f} KB)")
            
        except Exception as e:
            print(f"      - Error processing {filename}: {e}")

    return issued_vcs

# === 4. 接口定义 ===

@app.route('/issue_vc', methods=['POST'])
def handle_issue_vc():
    """
    接收申请 -> 验签 -> 模拟耗时 -> 批量发证
    """
    try:
        data = request.json
        applicant_did = data.get('applicant')
        signature = data.get('signature')
        
        print(f"\n>>> [Request] VC Application from: {applicant_did}")

        # --- A. 验证身份 ---
        if not applicant_did or not signature:
            return jsonify({"error": "Missing applicant or signature"}), 400
        
        # 还原原始消息进行验签
        payload_copy = data.copy()
        if 'signature' in payload_copy: del payload_copy['signature']
        serialized_payload = json.dumps(payload_copy, sort_keys=True, separators=(',', ':'))
        
        # 验证：签名者必须是 applicant DID 的合法控制者
        is_valid, reason = validator.verify_request_signature(serialized_payload, signature, applicant_did)
        
        if not is_valid:
            print(f"    [Auth Fail] {reason}")
            return jsonify({"error": f"Signature verification failed: {reason}"}), 401
        
        # 打印签名通过
        print("    [Auth Success] 签名通过")

        # --- B. 模拟审批 ---
        # Sleep 2秒并打印
        time.sleep(2)
        print("    [Process] 假设申请者身份属性验证已通过，正在签发VCs……")

        # --- C. 批量签发所有证书 ---
        vc_list = generate_all_vcs(applicant_did)
        
        print(f"    [Issued] Successfully issued {len(vc_list)} VCs to {applicant_did}")
        
        # 返回列表
        return jsonify(vc_list)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8000, threaded=True)
