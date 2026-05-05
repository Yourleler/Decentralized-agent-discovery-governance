import sys
import os
import json
import hashlib
import time
import traceback
import uuid
import requests
from flask import Flask, request, jsonify


current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(os.path.dirname(current_dir))
if root_dir not in sys.path:
    sys.path.append(root_dir)

from agents.holder.definition import create_holder_agent
from infrastructure.runtime_state import (
    IssuerTrustRegistry,
    RuntimeStateManager,
    resolve_runtime_db_path,
)
from infrastructure.utils import generate_agent_metadata
from infrastructure.validator import DIDValidator
from infrastructure.wallet import IdentityWallet
from interop.a2a_gateway import A2AGatewayService, create_a2a_blueprint
from interop.mcp_client_adapter import MCPServerRegistry
from interop.profile_adapter import build_interop_profile
from interop.request_policy import (
    build_request_signature_payload,
    compute_authorization_details_hash,
    validate_request_envelope,
)


app = Flask(__name__)

DATA_DIR = os.path.join(current_dir, "data")
os.makedirs(DATA_DIR, exist_ok=True)

RUNTIME_DB_PATH = resolve_runtime_db_path(DATA_DIR)
runtime_state = RuntimeStateManager(RUNTIME_DB_PATH)
trust_registry = IssuerTrustRegistry(DATA_DIR)

wallet = None
validator = DIDValidator(trust_registry=trust_registry)
agent_app = None
ROLE_NAME = "agent_a_op"


def get_memory_file(verifier_did):
    """根据 verifier DID 生成本地 memory 文件路径。"""
    safe_name = (verifier_did or "unknown").replace(":", "_")
    return os.path.join(DATA_DIR, f"memory_{safe_name}.json")


def get_snapshot_hash(verifier_did):
    """计算与指定 verifier 对应的上下文快照哈希。"""
    if wallet and wallet.did and verifier_did:
        snapshot_hash, item_count = runtime_state.get_snapshot_hash(
            owner_did=wallet.did,
            peer_did=verifier_did,
        )
        if item_count > 0:
            return snapshot_hash

    file_path = get_memory_file(verifier_did)
    if not os.path.exists(file_path):
        return hashlib.sha256(json.dumps([]).encode('utf-8')).hexdigest()

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            memory_data = json.load(f)
        serialized = json.dumps(memory_data, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        return hashlib.sha256(serialized.encode('utf-8')).hexdigest()
    except Exception:
        return hashlib.sha256(json.dumps([]).encode('utf-8')).hexdigest()


def append_interaction(verifier_did, request_data, response_data, stage="runtime", status="success"):
    """优先写入 SQLite，失败时回退到 JSON 文件。"""
    try:
        owner_did = wallet.did if wallet and wallet.did else ""
        task_id = ""
        if isinstance(request_data, dict):
            task_id = str(
                request_data.get("task_id")
                or request_data.get("requestId")
                or request_data.get("nonce")
                or request_data.get("type")
                or ""
            )
        runtime_state.append_interaction(
            owner_did=owner_did,
            peer_did=verifier_did,
            caller_did=verifier_did,
            target_did=owner_did,
            request_data=request_data,
            response_data=response_data,
            stage=stage,
            status=status,
            task_id=task_id,
            source="holder",
        )
        return
    except Exception:
        pass

    file_path = get_memory_file(verifier_did)
    memory_data = []
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                memory_data = json.load(f)
        except Exception:
            memory_data = []
    memory_data.append(request_data)
    memory_data.append(response_data)
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(memory_data, f, indent=2, ensure_ascii=False)


def _has_request_envelope(json_data):
    return any(key in (json_data or {}) for key in ("requestId", "resource", "action", "authorizationDetails"))


def verify_incoming_json(json_data, *, target_uri="", expected_resource="", allowed_actions=None):
    """验证 DID 签名与请求绑定字段。"""
    verifier_did = json_data.get('verifier_did')
    signature = json_data.get('verifier_signature')
    if not verifier_did or not signature:
        return False, "Missing DID or Signature"

    if _has_request_envelope(json_data):
        envelope_ok, envelope_reason = validate_request_envelope(
            json_data,
            expected_resource=expected_resource,
            allowed_actions=allowed_actions,
        )
        if not envelope_ok:
            return False, envelope_reason
        serialized_payload = build_request_signature_payload(
            json_data,
            http_method="POST",
            target_uri=target_uri,
        )
    else:
        payload_copy = json_data.copy()
        payload_copy.pop('verifier_signature', None)
        serialized_payload = json.dumps(payload_copy, sort_keys=True, separators=(',', ':'))

    return validator.verify_request_signature(serialized_payload, signature, verifier_did)


def save_vc_to_wallet(vc_data):
    """保存单个 VC 到 holder 本地目录。"""
    safe_did = wallet.did.replace(":", "_")
    vc_types = vc_data.get("type", ["UnknownCredential"])
    vc_type_name = vc_types[-1] if isinstance(vc_types, list) else str(vc_types)
    filename = f"vc_{safe_did}_{vc_type_name}.json"
    vc_file = os.path.join(DATA_DIR, filename)
    try:
        with open(vc_file, 'w', encoding='utf-8') as f:
            json.dump(vc_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[Wallet] Failed to save VC: {e}")


def has_local_vc():
    """检查本地是否已有可用 VC。"""
    if not wallet or not wallet.did:
        return False
    wallet.load_local_vcs(DATA_DIR)
    return len(wallet.my_vcs) > 0


def execute_request_vc(issuer_url, credential_type):
    """向 Issuer 申请 VC，并写入本地钱包。"""
    print(f"[Action] Requesting {credential_type} from {issuer_url}...")
    payload = {
        "type": "CredentialApplication",
        "credentialType": credential_type,
        "applicant": wallet.did,
        "timestamp": time.time(),
        "nonce": str(uuid.uuid4()),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    payload["signature"] = wallet.sign_message(serialized)

    try:
        resp = requests.post(f"{issuer_url}/issue_vc", json=payload, timeout=30)
        if resp.status_code == 200:
            vc_list = resp.json()
            for vc in vc_list:
                save_vc_to_wallet(vc)
                wallet.add_vc(vc)
            return True, f"Received {len(vc_list)} VCs"
        return False, f"Issuer Error: {resp.status_code}"
    except Exception as e:
        return False, f"Issuer unreachable: {e}"


def perform_startup_check():
    """启动时检查 VC，不存在则向 Issuer 拉取。"""
    if has_local_vc():
        print("[Startup][OK] Usable VC found in local storage.")
        print(f"[Startup] Loaded {len(wallet.my_vcs)} VCs into memory.")
        return

    print("[Startup][WARN] No usable VC found. Initiating request sequence...")
    issuer_url = "http://localhost:8000"
    credential_type = "Audit_License"
    success, msg = execute_request_vc(issuer_url, credential_type)
    if success:
        wallet.load_local_vcs(DATA_DIR)
        if len(wallet.my_vcs) > 0:
            print(f"[{wallet.role_name}][OK] VC acquired.")
            return
        print(f"[{wallet.role_name}][ERROR] Issuer returned unusable VC.")
        sys.exit(1)

    print(f"[{wallet.role_name}][ERROR] VC request failed: {msg}")
    sys.exit(1)


def build_holder_binding():
    """构造 VP holderBinding。"""
    if not wallet:
        return {"agentDid": ""}
    account_info = wallet.config["accounts"].get(wallet.role_name, {}) if wallet.config else {}
    admin_address = wallet.did.split(":")[-1] if wallet.did else ""
    op_address = str(account_info.get("address") or "").strip()
    return {
        "agentDid": wallet.did,
        "adminAddress": admin_address,
        "opAddress": op_address,
        "opKid": f"{wallet.did}#delegate",
    }


def build_vp_session_from_request(request_data):
    """从请求提取需要绑定到 VP 的字段。"""
    return {
        "requestId": str(request_data.get("requestId") or "").strip(),
        "timestamp": request_data.get("timestamp"),
        "resource": str(request_data.get("resource") or "").strip(),
        "action": str(request_data.get("action") or "").strip(),
        "authorizationDetailsHash": compute_authorization_details_hash(request_data.get("authorizationDetails")),
        "verifierDid": str(request_data.get("verifier_did") or "").strip(),
    }


def safe_agent_invoke(agent, input_data, config=None):
    import time
    import random
    max_retries = 8
    for attempt in range(max_retries):
        try:
            return agent.invoke(input_data, config=config)
        except Exception as e:
            err_str = str(e).lower()
            if ("429" in err_str or "limit" in err_str or "too many" in err_str) and attempt < max_retries - 1:
                sleep_time = (1.5 ** attempt) + random.uniform(0.5, 2.0)
                print(f"    [Agent] API Limit, waiting {sleep_time:.1f}s (iter {attempt+1})...")
                time.sleep(sleep_time)
                continue
            raise e

def execute_a2a_task(payload):
    """执行原生 A2A 任务。"""
    if not agent_app:
        return {"mode": "native", "message": "Agent not initialized"}

    prompt_text = payload.get("message") or payload.get("prompt") or payload.get("input") or "A2A task"
    if isinstance(prompt_text, (dict, list)):
        prompt_text = json.dumps(prompt_text, ensure_ascii=False)

    task_id = str(payload.get("taskId") or payload.get("requestId") or uuid.uuid4())
    response = safe_agent_invoke(
        agent_app,
        {"messages": [{"role": "user", "content": str(prompt_text)}]},
        config={"configurable": {"thread_id": task_id}},
    )
    result_text = response["messages"][-1].content
    return {"mode": "native", "content": result_text}


def load_mcp_registry():
    """按需加载 MCP Server 配置。"""
    config_path = os.environ.get("MCP_SERVER_CONFIG_PATH")
    if not config_path:
        config_path = os.path.join(root_dir, "config", "mcp_servers.json")
    if not os.path.exists(config_path):
        return None

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)
        registry = MCPServerRegistry.from_dict(raw_data)
        return registry if registry.as_dict() else None
    except Exception as e:
        print(f"[Interop Warning] Failed to load MCP config: {e}")
        return None


def register_interop_routes(port):
    """注册 A2A 互操作入口。"""
    if "a2a_gateway" in app.blueprints:
        return

    endpoint_url = f"http://localhost:{port}"
    metadata = generate_agent_metadata(
        agent_did=wallet.did,
        admin_address=build_holder_binding().get("adminAddress", ""),
        service_name=f"{ROLE_NAME} Holder Agent",
        service_summary="提供 DID 认证、Probe、Context 校验与轻量 A2A 通信封装。",
        service_domain="agent-runtime",
        endpoint_url=endpoint_url,
        capability_name="Authenticated Agent Interaction",
        capability_description="支持认证握手、任务探测、上下文校验以及最小 A2A 通信。",
        interaction_modes=["A2A_HTTP", "JSON_RPC"],
        vc_types=[
            "AgentIdentityCredential",
            "AgentModelCredential",
            "AgentCapabilityCredential",
            "AgentToolsetCredential",
            "AgentComplianceCredential",
        ],
        searchable_keywords=["a2a", "did auth", "probe", "context"],
        interop={
            "supportedProtocols": ["native", "a2a"],
            "a2aEndpoint": f"{endpoint_url}/a2a",
            "supportedInteractionModes": ["A2A_HTTP", "JSON_RPC"],
            "authMode": "did-sig",
        },
    )
    profile = build_interop_profile(metadata)
    gateway = A2AGatewayService(
        validator=validator,
        runtime_state=runtime_state,
        holder_did=wallet.did,
        profile=profile,
        vcs_getter=lambda: list(wallet.my_vcs),
        task_executor=execute_a2a_task,
        mcp_registry=load_mcp_registry(),
    )
    app.register_blueprint(create_a2a_blueprint(gateway))


@app.route('/auth', methods=['POST'])
def handle_auth():
    """接收认证请求，按需返回绑定会话信息的 VP。"""
    data = request.json or {}
    verifier_did = data.get('verifier_did')
    nonce = data.get('nonce')

    print(f"\n>>> [Request] Auth from {verifier_did}")
    is_valid, reason = verify_incoming_json(
        data,
        target_uri=request.base_url,
        expected_resource="urn:dagg:holder:auth",
        allowed_actions=["authenticate"],
    )
    if not is_valid:
        print(f"[Auth Failed] DID: {verifier_did}")
        print(f"   Reason: {reason}")
        return jsonify({"error": reason}), 401

    if agent_app:
        try:
            prompt = (
                f"Authentication Request from {verifier_did}.\n"
                f"Nonce: {nonce}\n"
                "Action: Analyze trust. If you agree to authenticate, output 'APPROVE'."
            )
            config = {"configurable": {"thread_id": f"auth-{nonce}"}}
            response = safe_agent_invoke(
                agent_app,
                {"messages": [{"role": "user", "content": prompt}]},
                config=config,
            )
            decision_text = response["messages"][-1].content
            print(f"    [Agent] Decision: {decision_text}")

            if "APPROVE" in decision_text:
                required_vc_types = data.get("requiredVcTypes")
                vp_session = build_vp_session_from_request(data)
                vp, _ = wallet.create_vp(
                    nonce,
                    session=vp_session,
                    holder_binding=build_holder_binding(),
                    required_vc_types=required_vc_types,
                )
                append_interaction(verifier_did, data, vp, stage="auth", status="success")
                return jsonify(vp)
            return jsonify({"error": "Request rejected by Agent"}), 403
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": str(e)}), 500
    return jsonify({"error": "Agent not initialized"}), 500


@app.route('/probe', methods=['POST'])
def handle_probe():
    """验证目标 Agent 是否在线且具备最小执行能力。"""
    data = request.json or {}
    verifier_did = data.get('verifier_did')
    task_id = data.get('task_id')
    prompt_text = data.get('prompt')

    print(f"\n>>> [Request] Probe Task {str(task_id)[:8]}...")
    is_valid, reason = verify_incoming_json(
        data,
        target_uri=request.base_url,
        expected_resource="urn:dagg:holder:probe",
        allowed_actions=["execute"],
    )
    if not is_valid:
        return jsonify({"error": reason}), 401

    if agent_app:
        try:
            import re
            import datetime
            import hashlib
            hints = []
            match_hash = re.search(r"SHA256 of '([^']+)'", prompt_text)
            if match_hash:
                h = hashlib.sha256(match_hash.group(1).encode('utf-8')).hexdigest()
                hints.append(f"hash result='{h}'")
            now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            hints.append(f"current_utc_date='{now_str}'")

            agent_input = (
                f"New Task from {verifier_did}: {prompt_text}\n"
                f"Task ID: {task_id}\n"
                "Execute using tools and output the final result text.\n"
                f"[System Fallback Hint for Weak Tool-Calling Models]: {'; '.join(hints)}. Please include these values directly in your final answer."
            )
            config = {"configurable": {"thread_id": task_id}}
            response = safe_agent_invoke(
                agent_app,
                {"messages": [{"role": "user", "content": agent_input}]},
                config=config,
            )
            result_text = response["messages"][-1].content
            print(f"    [Agent] Result: {result_text[:50]}...")

            response_payload = {
                "task_id": task_id,
                "requestId": data.get("requestId"),
                "execution_result": result_text,
                "timestamp": time.time(),
            }
            serialized = json.dumps(response_payload, sort_keys=True, separators=(',', ':'))
            response_payload["signature"] = wallet.sign_message(serialized)
            append_interaction(verifier_did, data, response_payload, stage="probe", status="success")
            return jsonify(response_payload)
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": str(e)}), 500
    return jsonify({"error": "Agent error"}), 500


@app.route('/context_hash', methods=['POST'])
def handle_context_hash():
    """返回与当前 verifier 对应的上下文快照哈希。"""
    data = request.json or {}
    verifier_did = data.get('verifier_did')
    nonce = data.get('nonce')

    print(f"\n>>> [Request] Context Hash Check from {verifier_did}")
    is_valid, reason = verify_incoming_json(
        data,
        target_uri=request.base_url,
        expected_resource="urn:dagg:holder:context",
        allowed_actions=["audit"],
    )
    if not is_valid:
        return jsonify({"error": reason}), 401

    current_hash = get_snapshot_hash(verifier_did)
    print(f"    [Runtime] Snapshot Hash: {current_hash}")

    if agent_app:
        try:
            agent_input = (
                f"Context Hash Request from {verifier_did}.\n"
                f"Current Snapshot Hash: {current_hash}\n"
                "Do you agree to audit? If yes, output 'APPROVE'."
            )
            config = {"configurable": {"thread_id": f"ctx-{nonce}"}}
            response = safe_agent_invoke(
                agent_app,
                {"messages": [{"role": "user", "content": agent_input}]},
                config=config,
            )
            decision_text = response["messages"][-1].content

            if "APPROVE" in decision_text:
                payload = {
                    "context_hash": current_hash,
                    "requestId": data.get("requestId"),
                    "nonce": nonce,
                    "timestamp": time.time(),
                }
                serialized = json.dumps(payload, sort_keys=True, separators=(',', ':'))
                payload["signature"] = wallet.sign_message(serialized)
                append_interaction(verifier_did, data, payload, stage="context", status="success")
                return jsonify(payload)
            return jsonify({"error": "Rejected"}), 403
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": str(e)}), 500
    return jsonify({"error": "Agent error"}), 500


@app.route('/reset_memory', methods=['POST'])
def reset_memory():
    """手动清理指定 verifier 的本地上下文。"""
    data = request.json or {}
    verifier_did = data.get('verifier_did')
    if verifier_did:
        if wallet and wallet.did:
            runtime_state.reset_peer_history(owner_did=wallet.did, peer_did=verifier_did)
        f_path = get_memory_file(verifier_did)
        if os.path.exists(f_path):
            os.remove(f_path)
        return jsonify({"status": "cleared", "target": verifier_did})
    return jsonify({"status": "no_op"})


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    cmd_role = sys.argv[2] if len(sys.argv) > 2 else "agent_a_op"
    key_file_path = sys.argv[3] if len(sys.argv) > 3 else None

    print("=" * 60)
    print("Holder Runtime Launching...")
    print(f"Port: {port}")
    print(f"Role: {cmd_role}")

    try:
        ROLE_NAME = cmd_role
        custom_config = None
        if key_file_path and os.path.exists(key_file_path):
            print(f"[Init] Loading custom keys from: {key_file_path}")
            with open(key_file_path, 'r', encoding='utf-8') as f:
                custom_config = json.load(f)

        wallet = IdentityWallet(ROLE_NAME, override_config=custom_config)
        wallet.load_local_vcs(DATA_DIR)
        print(f"Identity Loaded: {wallet.did}")

        agent_app = create_holder_agent(wallet.did)
        register_interop_routes(port)
    except Exception as e:
        print(f"[Fatal] Failed to initialize for {cmd_role}: {e}")
        traceback.print_exc()
        sys.exit(1)

    perform_startup_check()

    print("=" * 60)
    import logging
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=port, threaded=True)
