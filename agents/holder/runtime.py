import sys
import os
import json
import hashlib
import time
import traceback
import uuid
import requests
from flask import Flask, request, jsonify

# === 路径设置 ===
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(os.path.dirname(current_dir))
if root_dir not in sys.path:
    sys.path.append(root_dir)

# === 引入 Infrastructure 和 Agent ===
from infrastructure.wallet import IdentityWallet
from infrastructure.validator import DIDValidator
from agents.holder.definition import create_holder_agent

app = Flask(__name__)

# === 1. 初始化 Runtime 组件 ===

DATA_DIR = os.path.join(current_dir, "data")
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# === 全局变量占位 ===
# 实际初始化将在 __main__ 中根据参数决定，或者在 import 时加载默认值
wallet = None
validator = DIDValidator()#DID验证器
agent_app = None
ROLE_NAME = "agent_a_op" # 默认值

# === 2. 内存管理 ===
def get_memory_file(verifier_did):
    """
    根据 verifier 的 DID，生成一个专属的 memory 文件路径
    """
    if not verifier_did: verifier_did = "unknown"
    safe_name = verifier_did.replace(":", "_")
    return os.path.join(DATA_DIR, f"memory_{safe_name}.json")

def get_snapshot_hash(verifier_did):
    """
    对当前 verifier 对应的 memory 文件，算一个确定性的 SHA-256 哈希
    """
    file_path = get_memory_file(verifier_did)
    if not os.path.exists(file_path):
        return hashlib.sha256(json.dumps([]).encode('utf-8')).hexdigest()
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            memory_data = json.load(f)
        #格式化,确保同内容哈希一致
        serialized = json.dumps(memory_data, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        return hashlib.sha256(serialized.encode('utf-8')).hexdigest()
    except Exception:
        return hashlib.sha256(json.dumps([]).encode('utf-8')).hexdigest()

def append_interaction(verifier_did, request_data, response_data):
    """
    把一次完整的“请求 + 响应”顺序追加到 memory 文件中
    """
    file_path = get_memory_file(verifier_did)
    memory_data = []
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                memory_data = json.load(f)
        except: memory_data = []
    memory_data.append(request_data)
    memory_data.append(response_data)
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(memory_data, f, indent=2, ensure_ascii=False)
    #print(f"[Memory] Interaction appended for {verifier_did}")

def verify_incoming_json(json_data):
    """
    检验DID正确和内容未篡改
    """
    verifier_did = json_data.get('verifier_did')
    signature = json_data.get('verifier_signature')
    if not verifier_did or not signature: return False, "Missing DID or Signature"
    payload_copy = json_data.copy()
    #移除签名,签名是对内容不带签名的json进行签名
    if 'verifier_signature' in payload_copy:
        del payload_copy['verifier_signature']
    serialized_payload = json.dumps(payload_copy, sort_keys=True, separators=(',', ':'))
    return validator.verify_request_signature(serialized_payload, signature, verifier_did)

# === 3. VC 管理与申请逻辑 (新增) ===

def save_vc_to_wallet(vc_data):
    """
    保存单个 VC 到本地 data 目录
    文件名格式: vc_{DID}_{VC_Type}.json
    """
    safe_did = wallet.did.replace(":", "_")
    vc_types = vc_data.get("type", ["UnknownCredential"])
    #把 VC 的 type 字段规范化成一个稳定、可用的类型名,[-1]是约定最后放最具体的
    vc_type_name = vc_types[-1] if isinstance(vc_types, list) else str(vc_types)
    filename = f"vc_{safe_did}_{vc_type_name}.json"
    vc_file = os.path.join(DATA_DIR, filename)
    try:
        with open(vc_file, 'w', encoding='utf-8') as f:
            json.dump(vc_data, f, indent=2, ensure_ascii=False)
        #print(f"[Wallet] VC Saved to: {vc_file}")
    except Exception as e:
        print(f"[Wallet] Failed to save VC: {e}")

def has_local_vc():
    """检查是否有本地 VC 文件"""
    import glob
    if not wallet or not wallet.did:
        return False
    safe_did = wallet.did.replace(":", "_")
    pattern = os.path.join(DATA_DIR, f"vc_{safe_did}_*.json")
    files = glob.glob(pattern)#用pattern匹配文件
    return len(files) > 0#找不到返回空list

def execute_request_vc(issuer_url, credential_type):
    """
    一个 DID 持有者，用带 nonce 的签名请求 Issuer 颁发 VC，并安全接收和存储它
    """
    print(f"[Action] Requesting {credential_type} from {issuer_url}...")
    
    payload = {
        "type": "CredentialApplication",
        "credentialType": credential_type,
        "applicant": wallet.did,
        "timestamp": time.time(),
        "nonce": str(uuid.uuid4())#防重放设计
    }
    
    serialized = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    payload["signature"] = wallet.sign_message(serialized)
    
    try:
        resp = requests.post(f"{issuer_url}/issue_vc", json=payload, timeout=30)
        if resp.status_code == 200:
            vc_list = resp.json() # 注意：Issuer 现在返回的是列表 List
            
            for vc in vc_list:
                save_vc_to_wallet(vc) # 1. 存盘
                wallet.add_vc(vc)     # 2. 加载到内存
            
            return True, f"Received {len(vc_list)} VCs"
        else:
            return False, f"Issuer Error: {resp.status_code}"
    except Exception as e:
        print(f"[Warning] Issuer unreachable ({e}). Simulating...")
        fake_vc = {
            "type": credential_type, 
            "credentialSubject": {"id": wallet.did}, 
            "mock": True
        }
        save_vc_to_wallet(fake_vc)
        wallet.add_vc(fake_vc) # 模拟的也要加载
        return True, "VC Simulated"

def perform_startup_check():
    """启动时的自检流程,检测VC是否存在,不存在则申请"""
    if has_local_vc():
        print("[Startup] ✅ VC found in local storage.")
        wallet.load_local_vcs(DATA_DIR)
        print(f"[Startup] Loaded {len(wallet.my_vcs)} VCs into memory.")
        return

    print("[Startup] ⚠️ No VC found. Initiating request sequence...")
    ISSUER_URL = "http://localhost:8000"
    CRED_TYPE = "Audit_License"
    
    success, msg = execute_request_vc(ISSUER_URL, CRED_TYPE)
    
    if success:
        print(f"[{wallet.role_name}] ✅ VC Acquired.")
    else:
        print(f"[{wallet.role_name}] ❌ VC Request Failed.")
        sys.exit(1)

# === 4. API 路由 ===

@app.route('/auth', methods=['POST'])
def handle_auth():
    """
    我是 verifier,我向你发起一次认证请求。
    你如果同意,请给我一份你当前身份和 VC 的可验证展示
    """
    data = request.json
    verifier_did = data.get('verifier_did')
    nonce = data.get('nonce')
    
    print(f"\n>>> [Request] Auth from {verifier_did}")
    is_valid, reason = verify_incoming_json(data)
    if not is_valid: 
        print(f"❌ [Auth Failed] DID: {verifier_did}")
        print(f"   Reason: {reason}")
        return jsonify({"error": reason}), 401

    if agent_app:
        try:
            prompt = (
                f"Authentication Request from {verifier_did}.\n"
                f"Nonce: {nonce}\n"
                "Action: Analyze trust. If you agree to authenticate, output 'APPROVE'."
            )#合法请求 = 同意” 是默认策略,为“可拒绝、可策略化、可审计的 Agent 行为”留接口
            config = {"configurable": {"thread_id": f"auth-{nonce}"}}
            response = agent_app.invoke(
                {"messages": [{"role": "user", "content": prompt}]},
                config=config
            )
            decision_text = response["messages"][-1].content
            print(f"    [Agent] Decision: {decision_text}")
            
            if "APPROVE" in decision_text:
                vp, duration = wallet.create_vp(nonce)
                append_interaction(verifier_did, data, vp)
                return jsonify(vp)
            else:
                return jsonify({"error": "Request rejected by Agent"}), 403
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": str(e)}), 500
    return jsonify({"error": "Agent not initialized"}), 500

@app.route('/probe', methods=['POST'])
def handle_probe():
    """
    验证是否是活着的、可执行的 Agent 实例 
    """
    data = request.json
    verifier_did = data.get('verifier_did')
    task_id = data.get('task_id')
    prompt_text = data.get('prompt')
    
    print(f"\n>>> [Request] Probe Task {task_id[:8]}...")
    is_valid, reason = verify_incoming_json(data)
    if not is_valid: return jsonify({"error": reason}), 401

    if agent_app:
        try:
            agent_input = (
                f"New Task from {verifier_did}: {prompt_text}\n"
                f"Task ID: {task_id}\n"
                "Execute using tools and output the final result text."
            )
            config = {"configurable": {"thread_id": task_id}}
            response = agent_app.invoke(
                {"messages": [{"role": "user", "content": agent_input}]},
                config=config
            )
            result_text = response["messages"][-1].content
            print(f"    [Agent] Result: {result_text[:50]}...")
            
            response_payload = {
                "task_id": task_id, "execution_result": result_text, "timestamp": time.time()
            }
            serialized = json.dumps(response_payload, sort_keys=True, separators=(',', ':'))
            response_payload["signature"] = wallet.sign_message(serialized)
            append_interaction(verifier_did, data, response_payload)
            return jsonify(response_payload)
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": str(e)}), 500
    return jsonify({"error": "Agent error"}), 500

@app.route('/context_hash', methods=['POST'])
def handle_context_hash():
    """
    上下文一致性审计,holder方把对应的verifier记录哈希返回
    """
    data = request.json
    verifier_did = data.get('verifier_did')
    nonce = data.get('nonce')
    
    print(f"\n>>> [Request] Context Hash Check from {verifier_did}")
    is_valid, reason = verify_incoming_json(data)
    if not is_valid: return jsonify({"error": reason}), 401
    
    current_hash = get_snapshot_hash(verifier_did)
    print(f"    [Runtime] Snapshot Hash: {current_hash}")

    if agent_app:
        try:
            agent_input = (
                f"Context Hash Request from {verifier_did}.\n"
                f"Current Snapshot Hash: {current_hash}\n"
                "Do you agree to audit? If yes, output 'APPROVE'."
            )
            # 添加 config 参数
            config = {"configurable": {"thread_id": f"ctx-{nonce}"}}
            response = agent_app.invoke(
                {"messages": [{"role": "user", "content": agent_input}]},
                config=config
            )
            decision_text = response["messages"][-1].content
            
            if "APPROVE" in decision_text:
                payload = {
                    "context_hash": current_hash, "nonce": nonce, "timestamp": time.time()
                }
                serialized = json.dumps(payload, sort_keys=True, separators=(',', ':'))
                payload["signature"] = wallet.sign_message(serialized)
                append_interaction(verifier_did, data, payload)
                return jsonify(payload)
            else:
                return jsonify({"error": "Rejected"}), 403
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": str(e)}), 500
    return jsonify({"error": "Agent error"}), 500

@app.route('/reset_memory', methods=['POST'])
def reset_memory():
    """
    手动清空某个 verifier 对应的本地 memory 文件
    """
    data = request.json or {}
    verifier_did = data.get('verifier_did')
    if verifier_did:
        f = get_memory_file(verifier_did)
        if os.path.exists(f):
            os.remove(f)
            return jsonify({"status": "cleared", "target": verifier_did})
    return jsonify({"status": "no_op"})

if __name__ == '__main__':
    # 参数解析
    # argv[1]: Port
    # argv[2]: Role Name (e.g., holder_1_op)
    # argv[3]: (Optional) Custom Key File Path
    
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    cmd_role = sys.argv[2] if len(sys.argv) > 2 else "agent_a_op"
    key_file_path = sys.argv[3] if len(sys.argv) > 3 else None
    
    print("="*60)
    print(f"Holder Runtime Launching...")
    print(f"Port: {port}")
    print(f"Role: {cmd_role}")

    # 动态初始化
    try:
        ROLE_NAME = cmd_role
        
        # 如果传入了特定的 key 文件（P2P 实验模式），则加载该配置
        custom_config = None
        if key_file_path and os.path.exists(key_file_path):
            print(f"[Init] Loading custom keys from: {key_file_path}")
            with open(key_file_path, 'r', encoding='utf-8') as f:
                custom_config = json.load(f)
        
        # 初始化钱包
        wallet = IdentityWallet(ROLE_NAME, override_config=custom_config)
        wallet.load_local_vcs(DATA_DIR)
        print(f"Identity Loaded: {wallet.did}")
        
        # 初始化 Agent
        agent_app = create_holder_agent(wallet.did)
        
    except Exception as e:
        print(f"[Fatal] Failed to initialize for {cmd_role}: {e}")
        traceback.print_exc()
        sys.exit(1)

    # 启动检查 (VC 申请)
    perform_startup_check()

    print("="*60)
    # 禁用 Flask 的 Startup Banner 以减少 100 个进程时的控制台噪音
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    app.run(host='0.0.0.0', port=port, threaded=True)#接受所有IP的请求
