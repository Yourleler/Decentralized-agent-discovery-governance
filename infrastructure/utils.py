import json
import hashlib
import itertools
import datetime
from web3 import Web3

from .load_config import load_key_config

_RPC_CYCLE = None
_RPC_CYCLE_KEY = ""

ETH_PRICE_USD = 2930.0  # 2026/1/29


def _build_rpc_candidates(config):
    """
    功能：
    从配置中组装 RPC 候选列表，优先主 URL，再追加连接池并去重。

    参数：
    config (dict): 配置字典。

    返回值：
    list[str]: 去重后的 RPC URL 列表。
    """
    primary = str(config.get("api_url") or "").strip()
    pool = config.get("api_url_pool")

    merged = []
    if primary:
        merged.append(primary)
    if isinstance(pool, list):
        for item in pool:
            url = str(item).strip()
            if url:
                merged.append(url)

    deduped = []
    seen = set()
    for url in merged:
        if url not in seen:
            seen.add(url)
            deduped.append(url)
    return deduped


def get_rpc_url():
    """
    功能：
    获取一个 RPC URL，按“主 URL 优先 + 顺序轮询”策略返回。

    参数：
    无。

    返回值：
    tuple[str, dict]: (选中的 RPC URL, 配置字典)。
    """
    global _RPC_CYCLE, _RPC_CYCLE_KEY
    config = load_key_config()
    candidates = _build_rpc_candidates(config)
    if not candidates:
        raise ValueError("配置缺少可用 RPC URL（api_url/api_url_pool 均为空）")

    cycle_key = "|".join(candidates)
    if _RPC_CYCLE is None or _RPC_CYCLE_KEY != cycle_key:
        _RPC_CYCLE = itertools.cycle(candidates)
        _RPC_CYCLE_KEY = cycle_key

    selected_url = next(_RPC_CYCLE)
    return selected_url, config


def get_w3():
    """
    功能：
    初始化 Web3 连接，优先使用主 RPC，失败时自动回退到备用 RPC。

    参数：
    无。

    返回值：
    tuple[Web3, dict]: (可用 Web3 实例, 配置字典)。
    """
    try:
        config = load_key_config()
        candidates = _build_rpc_candidates(config)
        if not candidates:
            print("[Network] 配置缺少可用 RPC URL（api_url/api_url_pool 均为空）")
            exit(1)

        errors = []
        for rpc_url in candidates:
            try:
                provider = Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 8})
                w3 = Web3(provider)
                if w3.is_connected():
                    config["api_url"] = rpc_url
                    return w3, config
                errors.append(f"{rpc_url} -> not connected")
            except Exception as inner_exc:
                errors.append(f"{rpc_url} -> {inner_exc}")

        print("[Network] 所有 RPC 节点均不可达：")
        for err in errors:
            print(f"[Network]   {err}")
        exit(1)
    except Exception as e:
        print(f"[Network] 初始化异常: {e}")
        exit(1)

# ethr:did registry 地址（Sepolia）
REGISTRY_ADDRESS = "0x03d5003bf0e79C5F5223588F347ebA39AfbC3818"

# ERC-1056 DID Registry ABI
REGISTRY_ABI = [
    {
        "constant": False,
        "inputs": [
            {"internalType": "address", "name": "identity", "type": "address"},
            {"internalType": "address", "name": "newOwner", "type": "address"}
        ],
        "name": "changeOwner",
        "outputs": [],
        "payable": False,
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [{"internalType": "address", "name": "identity", "type": "address"}],
        "name": "identityOwner",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [
            {"internalType": "address", "name": "identity", "type": "address"},
            {"internalType": "bytes32", "name": "name", "type": "bytes32"},
            {"internalType": "bytes", "name": "value", "type": "bytes"},
            {"internalType": "uint256", "name": "validity", "type": "uint256"}
        ],
        "name": "setAttribute",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "address", "name": "identity", "type": "address"},
            {"indexed": False, "internalType": "address", "name": "owner", "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "previousChange", "type": "uint256"}
        ],
        "name": "DIDOwnerChanged",
        "type": "event"
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "address", "name": "identity", "type": "address"},
            {"indexed": False, "internalType": "bytes32", "name": "name", "type": "bytes32"},
            {"indexed": False, "internalType": "bytes", "name": "value", "type": "bytes"},
            {"indexed": False, "internalType": "uint256", "name": "validTo", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "previousChange", "type": "uint256"}
        ],
        "name": "DIDAttributeChanged",
        "type": "event"
    },
    {
        "constant": True,
        "inputs": [
            {"internalType": "address", "name": "identity", "type": "address"},
            {"internalType": "bytes32", "name": "delegateType", "type": "bytes32"},
            {"internalType": "address", "name": "delegate", "type": "address"}
        ],
        "name": "validDelegate",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function"
    }
]


def load_memory(file_path):
    """安全加载 JSON 文件，若不存在返回空列表"""
    import os
    if not os.path.exists(file_path):
        return []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Warning] Failed to load memory from {file_path}: {e}")
        return []


def save_memory(file_path, memory_data):
    """保存数据到 JSON 文件"""
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(memory_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Error] Failed to save memory to {file_path}: {e}")


def calculate_memory_hash(memory_data):
    """计算哈希，用于签名和校验"""
    serialized = json.dumps(
        memory_data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _utc_now_iso():
    """返回 UTC 时间（ISO 8601, Z 结尾）"""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_agent_metadata(
    agent_did,
    admin_address,
    service_name,
    service_summary,
    service_domain,
    endpoint_url,
    capability_name,
    capability_description,
    capability_id="cap.default.v1",
    capability_inputs=None,
    capability_outputs=None,
    capability_examples=None,
    tags=None,
    interaction_modes=None,
    vc_types=None,
    full_vc_refs=None,
    searchable_keywords=None,
    vector_text=None,
    metadata_version="2.0.0"
):
    """
    生成 metadata（对齐 config/agent_metadata_format.schema.json）

    参数说明:
        agent_did (str): Agent DID（链上注册主体）。
        admin_address (str): 管理员地址。
        service_name (str): 服务名称。
        service_summary (str): 服务摘要描述。
        service_domain (str): 服务领域，例如 finance。
        endpoint_url (str): 主服务端点 URL。
        capability_name (str): 能力名称。
        capability_description (str): 能力描述。
        capability_id (str): 能力唯一 ID。
        capability_inputs (list[str] | None): 能力输入字段列表。
        capability_outputs (list[str] | None): 能力输出字段列表。
        capability_examples (list[str] | None): 能力示例列表。
        tags (list[str] | None): 服务标签。
        interaction_modes (list[str] | None): 交互模式列表。
        vc_types (list[str] | None): 支持的 VC 类型列表。
        full_vc_refs (list[dict] | None): VC 引用信息列表。
        searchable_keywords (list[str] | None): 检索关键词。
        vector_text (str | None): 向量化文本；为空时自动生成。
        metadata_version (str): metadata 版本号。

    返回:
        dict: metadata payload。
    """
    now_iso = _utc_now_iso()

    if capability_inputs is None:
        capability_inputs = []
    if capability_outputs is None:
        capability_outputs = []
    if capability_examples is None:
        capability_examples = []
    if tags is None:
        tags = []
    if interaction_modes is None:
        interaction_modes = ["A2A_HTTP"]
    if vc_types is None:
        vc_types = ["AgentIdentityCredential"]
    if full_vc_refs is None:
        full_vc_refs = []
    if searchable_keywords is None:
        searchable_keywords = []
    if vector_text is None:
        vector_text = f"{service_summary}\nDomain: {service_domain}\nCapability: {capability_name}"

    return {
        "metadataVersion": metadata_version,
        "agentDid": agent_did,
        "adminAddress": admin_address,
        "service": {
            "name": service_name,
            "summary": service_summary,
            "domain": service_domain,
            "tags": tags,
            "interactionModes": interaction_modes,
            "endpoints": [
                {
                    "name": "primary-api",
                    "url": endpoint_url,
                    "protocol": "https",
                    "auth": "did-sig"
                }
            ]
        },
        "capabilities": [
            {
                "id": capability_id,
                "name": capability_name,
                "description": capability_description,
                "inputs": capability_inputs,
                "outputs": capability_outputs,
                "examples": capability_examples
            }
        ],
        "vcManifest": {
            "holderDid": agent_did,
            "types": vc_types,
            "lazyFetch": True,
            "fullVcRefs": full_vc_refs
        },
        "indexHints": {
            "vectorText": vector_text,
            "searchableKeywords": searchable_keywords
        },
        "timestamps": {
            "createdAt": now_iso,
            "updatedAt": now_iso
        }
    }


def generate_vc_payload(
    holder_did,
    issuer_did,
    vc_type="AgentIdentityCredential",
    subject_claims=None,
    valid_days=365,
    proof_jws=None,
    proof_type="EcdsaSecp256k1Signature2019",
    proof_purpose="assertionMethod",
    verification_method=None
):
    """
    生成 VC（对齐 config/vc_format.schema.json）

    参数说明:
        holder_did (str): 持有者 DID（写入 credentialSubject.id）。
        issuer_did (str): 发证者 DID。
        vc_type (str): VC 业务类型，例如 AgentIdentityCredential。
        subject_claims (dict | None): credentialSubject 的扩展 claims。
            注意：不允许覆盖 id/agentDid 为其他值。
        valid_days (int): 有效天数。
        proof_jws (str | None): 发证方真实签名（必填）。
        proof_type (str): proof.type。
        proof_purpose (str): proof.proofPurpose。
        verification_method (str | None): proof.verificationMethod；
            为空时默认 "{issuer_did}#controller"。

    返回:
        dict: VC payload。
    """
    if subject_claims is None:
        subject_claims = {}
    if not proof_jws:
        raise ValueError("proof_jws 不能为空，必须传入发证方真实签名。")

    now_dt = datetime.datetime.now(datetime.timezone.utc)
    valid_until_dt = now_dt + datetime.timedelta(days=valid_days)
    valid_from = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    valid_until = valid_until_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    if verification_method is None:
        verification_method = f"{issuer_did}#controller"

    credential_subject = {
        "id": holder_did,
        "agentDid": holder_did,
        "claimsVersion": "1.0.0"
    }
    safe_subject_claims = dict(subject_claims)
    if "id" in safe_subject_claims and safe_subject_claims["id"] != holder_did:
        raise ValueError("subject_claims.id 不能与 holder_did 不一致。")
    if "agentDid" in safe_subject_claims and safe_subject_claims["agentDid"] != holder_did:
        raise ValueError("subject_claims.agentDid 不能与 holder_did 不一致。")
    safe_subject_claims.pop("id", None)
    safe_subject_claims.pop("agentDid", None)
    credential_subject.update(safe_subject_claims)

    return {
        "@context": [
            "https://www.w3.org/ns/credentials/v2",
            "https://schema.org"
        ],
        "type": [
            "VerifiableCredential",
            vc_type
        ],
        "issuer": issuer_did,
        "validFrom": valid_from,
        "validUntil": valid_until,
        "credentialSubject": credential_subject,
        "proof": {
            "type": proof_type,
            "created": _utc_now_iso(),
            "verificationMethod": verification_method,
            "proofPurpose": proof_purpose,
            "jws": proof_jws
        }
    }


def generate_vp_payload(
    holder_did,
    nonce,
    verifiable_credentials=None,
    holder_binding=None,
    proof_jws=None,
    proof_type="EcdsaSecp256k1RecoverySignature2020",
    proof_purpose="authentication",
    verification_method=None
):
    """
    生成 VP（对齐 config/vp_format.schema.json）

    参数说明:
        holder_did (str): VP 持有者 DID。
        nonce (str): verifier 下发的 challenge/nonce。
        verifiable_credentials (list[dict] | None): 要携带的 VC 列表。
        holder_binding (dict | None): 持有者绑定信息；
            为空时默认仅包含 {"agentDid": holder_did}。
        proof_jws (str | None): holder 真实签名（必填）。
        proof_type (str): proof.type。
        proof_purpose (str): proof.proofPurpose。
        verification_method (str | None): proof.verificationMethod；
            为空时默认 "{holder_did}#delegate"，与 wallet.create_vp 对齐。

    返回:
        dict: VP payload。
    """
    if verifiable_credentials is None:
        verifiable_credentials = []
    if holder_binding is None:
        holder_binding = {
            "agentDid": holder_did
        }
    if not proof_jws:
        raise ValueError("proof_jws 不能为空，必须传入 holder 真实签名。")
    if verification_method is None:
        verification_method = f"{holder_did}#delegate"

    return {
        "@context": [
            "https://www.w3.org/2018/credentials/v1"
        ],
        "type": [
            "VerifiablePresentation"
        ],
        "holder": holder_did,
        "holderBinding": holder_binding,
        "verifiableCredential": verifiable_credentials,
        "proof": {
            "type": proof_type,
            "created": _utc_now_iso(),
            "verificationMethod": verification_method,
            "proofPurpose": proof_purpose,
            "challenge": nonce,
            "jws": proof_jws
        }
    }
