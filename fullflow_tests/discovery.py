from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
from typing import Any

import requests
from web3 import Web3

from infrastructure.utils import generate_agent_metadata
from sidecar.services.discovery_service import DiscoveryService
from sidecar.services.sync_orchestrator import SyncOrchestrator
from sidecar.storage.sqlite_state import SQLiteStateStore
from sidecar.vector import SearchHit


def emit_progress(message: str) -> None:
    """
    功能：
    输出 discovery 阶段进度日志。

    参数：
    message (str): 进度消息文本。

    返回值：
    None: 仅打印日志，不返回数据。
    """
    ts = time.strftime("%H:%M:%S")
    print(f"[fullflow][{ts}][DISCOVERY] {message}", flush=True)


def build_case_assertion(
    case_id: str,
    capability_id: str,
    expected: str,
    actual: str,
    passed: bool,
    phase: str = "discovery",
    error: str = "",
) -> dict[str, Any]:
    """
    功能：
    构造统一用例断言结构，供 case_assertions.csv 汇总。

    参数：
    case_id (str): 用例唯一 ID。
    capability_id (str): 能力标识。
    expected (str): 期望描述。
    actual (str): 实际结果描述。
    passed (bool): 是否通过。
    phase (str): 所属阶段。
    error (str): 失败错误信息。

    返回值：
    dict[str, Any]: 统一断言字典。
    """
    return {
        "phase": phase,
        "case_id": case_id,
        "capability_id": capability_id,
        "expected": expected,
        "actual": actual,
        "passed": bool(passed),
        "error": error,
    }


def unique_subgraph_urls(primary_url: str, fallback_urls: list[str] | None) -> list[str]:
    """
    功能：
    组合并去重 Subgraph URL 列表，返回按优先级排序的候选地址。

    参数：
    primary_url (str): 主 Subgraph URL。
    fallback_urls (list[str] | None): 备选 Subgraph URL 列表。

    返回值：
    list[str]: 去重后的 URL 列表。
    """
    merged: list[str] = [primary_url] if primary_url.strip() else []
    for item in fallback_urls or []:
        text = str(item).strip()
        if text:
            merged.append(text)
    seen: set[str] = set()
    result: list[str] = []
    for url in merged:
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


AGENT_REGISTRY_V1_ABI: list[dict[str, Any]] = [
    {
        "inputs": [{"internalType": "address", "name": "", "type": "address"}],
        "name": "agents",
        "outputs": [
            {"internalType": "string", "name": "did", "type": "string"},
            {"internalType": "string", "name": "metadataCid", "type": "string"},
            {"internalType": "uint256", "name": "initScore", "type": "uint256"},
            {"internalType": "uint256", "name": "accumulatedPenalty", "type": "uint256"},
            {"internalType": "uint256", "name": "lastMisconductTimestamp", "type": "uint256"},
            {"internalType": "uint256", "name": "stakeAmount", "type": "uint256"},
            {"internalType": "bool", "name": "isSlashed", "type": "bool"},
            {"internalType": "bool", "name": "isRegistered", "type": "bool"},
            {"internalType": "bool", "name": "isfrozen", "type": "bool"},
            {"internalType": "address", "name": "admin", "type": "address"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "string", "name": "_did", "type": "string"},
            {"internalType": "string", "name": "_cid", "type": "string"},
        ],
        "name": "registerAgent",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "string", "name": "_newCid", "type": "string"}],
        "name": "updateServiceMetadata",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


class SimpleVectorIndex:
    """
    功能：
    提供最小可用的内存向量索引实现，供 Sidecar 发现链路测试使用。

    参数：
    无。

    返回值：
    无: 通过 upsert/delete/query 完成检索能力。
    """

    def __init__(self) -> None:
        """
        功能：
        初始化内存索引容器。

        参数：
        无。

        返回值：
        None: 创建空索引字典。
        """
        self._docs: dict[str, tuple[str, dict[str, Any]]] = {}

    def upsert(self, agent_id: str, vector_text: str, metadata: dict[str, Any] | None = None) -> None:
        """
        功能：
        写入或更新单条文档。

        参数：
        agent_id (str): 文档唯一标识。
        vector_text (str): 语义文本。
        metadata (dict[str, Any] | None): 文档元数据。

        返回值：
        None: 结果直接写入内存字典。
        """
        self._docs[agent_id] = (vector_text or "", metadata or {})

    def delete(self, agent_id: str) -> None:
        """
        功能：
        删除指定文档。

        参数：
        agent_id (str): 待删除文档 ID。

        返回值：
        None: 删除不存在的 ID 时静默返回。
        """
        self._docs.pop(agent_id, None)

    def query(self, text: str, top_k: int = 5, where: dict[str, Any] | None = None) -> list[SearchHit]:
        """
        功能：
        基于简单关键词重叠计算检索结果并返回命中列表。

        参数：
        text (str): 查询文本。
        top_k (int): 返回数量上限。
        where (dict[str, Any] | None): 过滤参数（当前未使用）。

        返回值：
        list[SearchHit]: 命中结果列表，score 越小越相关。
        """
        _ = where
        query_tokens = set((text or "").lower().split())
        scored: list[SearchHit] = []
        for agent_id, (doc, metadata) in self._docs.items():
            doc_tokens = set((doc or "").lower().split())
            overlap = len(query_tokens.intersection(doc_tokens))
            score = 1.0 / (1.0 + overlap)
            scored.append(
                SearchHit(
                    agent_id=agent_id,
                    score=score,
                    metadata=metadata,
                    document=doc,
                )
            )
        scored.sort(key=lambda item: item.score)
        return scored[: max(top_k, 1)]


class TimedVectorIndex:
    """
    包装向量索引，记录 upsert/delete/query 的耗时统计。
    """

    def __init__(self, delegate: Any, backend_name: str) -> None:
        self._delegate = delegate
        self._backend_name = backend_name
        self._stats: dict[str, dict[str, float]] = {
            "upsert": {"count": 0.0, "total_ms": 0.0, "max_ms": 0.0, "total_results": 0.0},
            "delete": {"count": 0.0, "total_ms": 0.0, "max_ms": 0.0, "total_results": 0.0},
            "query": {"count": 0.0, "total_ms": 0.0, "max_ms": 0.0, "total_results": 0.0},
        }
        self._last_query_latency_ms = 0.0
        self._last_query_result_count = 0

    def _record(self, operation: str, elapsed_ms: float, result_count: int = 0) -> None:
        bucket = self._stats[operation]
        bucket["count"] += 1.0
        bucket["total_ms"] += float(elapsed_ms)
        bucket["max_ms"] = max(bucket["max_ms"], float(elapsed_ms))
        bucket["total_results"] += float(max(result_count, 0))

    def upsert(self, agent_id: str, vector_text: str, metadata: dict[str, Any] | None = None) -> None:
        started = time.perf_counter()
        self._delegate.upsert(agent_id=agent_id, vector_text=vector_text, metadata=metadata)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self._record("upsert", elapsed_ms, result_count=0)

    def delete(self, agent_id: str) -> None:
        started = time.perf_counter()
        self._delegate.delete(agent_id=agent_id)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self._record("delete", elapsed_ms, result_count=0)

    def query(self, text: str, top_k: int = 5, where: dict[str, Any] | None = None) -> list[SearchHit]:
        started = time.perf_counter()
        results = self._delegate.query(text=text, top_k=top_k, where=where)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        result_count = len(results)
        self._last_query_latency_ms = elapsed_ms
        self._last_query_result_count = result_count
        self._record("query", elapsed_ms, result_count=result_count)
        return results

    def get_backend_name(self) -> str:
        return self._backend_name

    def get_last_query_latency_ms(self) -> float:
        return self._last_query_latency_ms

    def get_last_query_result_count(self) -> int:
        return self._last_query_result_count

    def build_summary_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for operation in ("upsert", "delete", "query"):
            bucket = self._stats[operation]
            count = int(bucket["count"])
            mean_ms = float(bucket["total_ms"]) / float(count) if count > 0 else 0.0
            rows.append(
                {
                    "vector_backend": self._backend_name,
                    "vector_operation": operation,
                    "vector_call_count": count,
                    "vector_total_latency_ms": float(bucket["total_ms"]),
                    "vector_mean_latency_ms": mean_ms,
                    "vector_p95_latency_ms": mean_ms,  # 轻量统计，样本量小时用均值近似
                    "vector_max_latency_ms": float(bucket["max_ms"]),
                    "vector_total_results": int(bucket["total_results"]),
                }
            )
        return rows


def to_did(address: str) -> str:
    """
    功能：
    将地址转换为 did:ethr:sepolia DID 字符串。

    参数：
    address (str): 以太坊地址。

    返回值：
    str: DID 字符串。
    """
    return f"did:ethr:sepolia:{address}"


def build_tx_metric(
    category: str,
    actor: str,
    tx_hash: str,
    receipt: dict[str, Any],
    gas_price_wei: int,
    latency_seconds: float,
    note: str = "",
    case_id: str = "",
) -> dict[str, Any]:
    """
    功能：
    构造发现阶段链上交易指标记录。

    参数：
    category (str): 指标分类。
    actor (str): 执行者标签。
    tx_hash (str): 交易哈希。
    receipt (dict[str, Any]): 交易回执。
    gas_price_wei (int): 交易 gas 单价。
    latency_seconds (float): 广播到确认耗时。
    note (str): 附加说明。

    返回值：
    dict[str, Any]: 统一结构指标字典。
    """
    gas_used = int(receipt.get("gasUsed", 0))
    effective = int(receipt.get("effectiveGasPrice", gas_price_wei))
    cost_eth = float(Web3.from_wei(gas_used * effective, "ether"))
    return {
        "category": category,
        "phase": "discovery",
        "case_id": case_id,
        "chain": "evm",
        "network": "sepolia",
        "tx_type": category,
        "actor": actor,
        "tx_hash": tx_hash,
        "block_number": int(receipt.get("blockNumber", 0)),
        "gas_used": gas_used,
        "gas_price_wei": effective,
        "cost_eth": cost_eth,
        "latency_seconds": float(latency_seconds),
        "status": int(receipt.get("status", 0)),
        "note": note,
    }


def cache_metadata_as_local_cid(metadata: dict[str, Any]) -> tuple[str, float, int]:
    """
    功能：
    将 metadata JSON 写入本地 .ipfs_cache，并返回本地 CID 字符串。

    参数：
    metadata (dict[str, Any]): metadata 数据对象。

    返回值：
    str: 本地 CID 标识字符串。
    """
    raw = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    cid = f"local-fullflow-{digest[:40]}"
    cache_path = Path(".ipfs_cache").resolve()
    cache_path.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    (cache_path / cid).write_bytes(raw)
    elapsed = time.perf_counter() - started
    return cid, elapsed, len(raw)


def load_local_cid_payload(cid: str) -> tuple[dict[str, Any], float, int]:
    """
    从本地 .ipfs_cache 读取 CID 文件，用于统计“下载”口径耗时。
    """
    cache_path = Path(".ipfs_cache").resolve() / cid
    started = time.perf_counter()
    raw = cache_path.read_bytes()
    elapsed = time.perf_counter() - started
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"CID 内容不是对象: {cid}")
    return payload, elapsed, len(raw)


def make_metadata_deterministic(metadata: dict[str, Any], stable_tag: str) -> dict[str, Any]:
    """
    功能：
    将 metadata 中的时间相关字段固定化，确保同一输入生成稳定 CID。

    参数：
    metadata (dict[str, Any]): 原始 metadata 对象。
    stable_tag (str): 稳定标签，用于区分不同 Agent。

    返回值：
    dict[str, Any]: 处理后的 metadata 对象。
    """
    output = dict(metadata)
    output["stableTag"] = stable_tag
    timestamps = output.get("timestamps")
    if not isinstance(timestamps, dict):
        timestamps = {}
    timestamps["createdAt"] = "2026-01-01T00:00:00Z"
    timestamps["updatedAt"] = "2026-01-01T00:00:00Z"
    output["timestamps"] = timestamps
    return output


def send_contract_tx(
    w3: Web3,
    tx: dict[str, Any],
    private_key: str,
    timeout_seconds: int = 300,
) -> tuple[str, dict[str, Any], float]:
    """
    功能：
    签名并发送合约交易，等待交易回执。

    参数：
    w3 (Web3): Web3 实例。
    tx (dict[str, Any]): 待签名交易字典。
    private_key (str): 签名私钥。
    timeout_seconds (int): 回执等待超时。

    返回值：
    tuple[str, dict[str, Any], float]: (tx_hash_hex, receipt_dict, latency_seconds)。
    """
    started = time.time()
    signed = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout_seconds)
    return w3.to_hex(tx_hash), dict(receipt), time.time() - started


def ensure_admin_balance(
    w3: Web3,
    admin_address: str,
    required_value_eth: float,
    gas_limit: int,
    gas_price_wei: int,
    reserve_eth: float,
    funder_info: dict[str, str] | None,
    chain_tx_metrics: list[dict[str, Any]],
    actor_role: str,
    case_id: str = "",
) -> None:
    """
    功能：
    确保 Admin 地址余额足够覆盖“交易价值+gas+预留金”，不足时由主账户自动补币。

    参数：
    w3 (Web3): Web3 实例。
    admin_address (str): 目标 Admin 地址。
    required_value_eth (float): 交易 value（ETH），例如 register 的 stake。
    gas_limit (int): 交易 gas 上限。
    gas_price_wei (int): 当前交易 gasPrice。
    reserve_eth (float): 额外预留金额（ETH）。
    funder_info (dict[str, str] | None): 主账户信息（address/private_key）。
    chain_tx_metrics (list[dict[str, Any]]): 链上指标列表（就地追加）。
    actor_role (str): 当前目标角色名。

    返回值：
    None: 余额满足条件后返回；若无法补币则抛出异常。
    """
    current_balance = int(w3.eth.get_balance(admin_address))
    required_wei = int(w3.to_wei(required_value_eth + reserve_eth, "ether")) + int(gas_limit * gas_price_wei)
    if current_balance >= required_wei:
        return

    shortfall_wei = required_wei - current_balance
    if not funder_info:
        raise RuntimeError(
            f"{actor_role} 余额不足且无法自动补币: balance={current_balance}, required={required_wei}"
        )

    funder_address = str(funder_info.get("address", ""))
    funder_private_key = str(funder_info.get("private_key", ""))
    if not funder_address or not funder_private_key:
        raise RuntimeError("主账户配置不完整，无法自动补币")

    chain_id = int(w3.eth.chain_id)
    nonce = int(w3.eth.get_transaction_count(funder_address, "pending"))
    topup_gas_price = int(w3.eth.gas_price * 1.1)
    tx = {
        "chainId": chain_id,
        "nonce": nonce,
        "to": admin_address,
        "value": shortfall_wei,
        "gas": 21000,
        "gasPrice": topup_gas_price,
    }
    tx_hash, receipt, latency = send_contract_tx(w3=w3, tx=tx, private_key=funder_private_key)
    chain_tx_metrics.append(
        build_tx_metric(
            category="discovery_topup_admin",
            actor="master",
            tx_hash=tx_hash,
            receipt=receipt,
            gas_price_wei=topup_gas_price,
            latency_seconds=latency,
            note=f"target={actor_role} amount_wei={shortfall_wei}",
            case_id=case_id,
        )
    )


def register_or_update_agent(
    w3: Web3,
    registry_contract: Any,
    admin_address: str,
    admin_private_key: str,
    did: str,
    cid: str,
    stake_eth: float,
    chain_tx_metrics: list[dict[str, Any]],
    actor_role: str,
    funder_info: dict[str, str] | None = None,
    balance_reserve_eth: float = 0.002,
    case_id: str = "",
) -> tuple[int, str]:
    """
    功能：
    针对目标 Admin 地址执行 registerAgent 或 updateServiceMetadata。

    参数：
    w3 (Web3): Web3 实例。
    registry_contract (Any): AgentRegistry_v1 合约实例。
    admin_address (str): Admin 地址。
    admin_private_key (str): Admin 私钥。
    did (str): 注册 DID。
    cid (str): metadata CID。
    stake_eth (float): 注册时质押金额（ETH）。
    chain_tx_metrics (list[dict[str, Any]]): 链上指标列表（就地追加）。
    actor_role (str): 执行者角色名。

    返回值：
    tuple[int, str]: (区块号, 执行动作)。
    """
    existing = registry_contract.functions.agents(Web3.to_checksum_address(admin_address)).call()
    is_registered = bool(existing[7])
    existing_cid = str(existing[1] or "")
    chain_id = int(w3.eth.chain_id)
    nonce = int(w3.eth.get_transaction_count(admin_address, "pending"))
    gas_price = int(w3.eth.gas_price * 1.15)

    if not is_registered:
        register_gas_limit = 500000
        ensure_admin_balance(
            w3=w3,
            admin_address=admin_address,
            required_value_eth=stake_eth,
            gas_limit=register_gas_limit,
            gas_price_wei=gas_price,
            reserve_eth=balance_reserve_eth,
            funder_info=funder_info,
            chain_tx_metrics=chain_tx_metrics,
            actor_role=actor_role,
            case_id=case_id,
        )
        tx = registry_contract.functions.registerAgent(did, cid).build_transaction(
            {
                "chainId": chain_id,
                "nonce": nonce,
                "gas": register_gas_limit,
                "gasPrice": gas_price,
                "value": w3.to_wei(stake_eth, "ether"),
            }
        )
        tx_hash, receipt, latency = send_contract_tx(w3, tx, admin_private_key)
        chain_tx_metrics.append(
            build_tx_metric(
                category="discovery_register_agent",
                actor=actor_role,
                tx_hash=tx_hash,
                receipt=receipt,
                gas_price_wei=gas_price,
                latency_seconds=latency,
                note=f"did={did} cid={cid}",
                case_id=case_id,
            )
        )
        return int(receipt.get("blockNumber", 0)), "register"

    if existing_cid == cid:
        emit_progress(f"{actor_role} 已是目标CID，跳过 update")
        return int(w3.eth.block_number), "noop"

    update_gas_limit = 250000
    ensure_admin_balance(
        w3=w3,
        admin_address=admin_address,
        required_value_eth=0.0,
        gas_limit=update_gas_limit,
        gas_price_wei=gas_price,
        reserve_eth=balance_reserve_eth,
        funder_info=funder_info,
        chain_tx_metrics=chain_tx_metrics,
        actor_role=actor_role,
        case_id=case_id,
    )
    tx = registry_contract.functions.updateServiceMetadata(cid).build_transaction(
        {
            "chainId": chain_id,
            "nonce": nonce,
            "gas": update_gas_limit,
            "gasPrice": gas_price,
            "value": 0,
        }
    )
    tx_hash, receipt, latency = send_contract_tx(w3, tx, admin_private_key)
    chain_tx_metrics.append(
        build_tx_metric(
            category="discovery_update_metadata",
            actor=actor_role,
            tx_hash=tx_hash,
            receipt=receipt,
            gas_price_wei=gas_price,
            latency_seconds=latency,
            note=f"cid={cid}",
            case_id=case_id,
        )
    )
    return int(receipt.get("blockNumber", 0)), "update"


def build_subgraph_headers(key_config: dict[str, Any]) -> dict[str, str]:
    """
    功能：
    构造查询 Subgraph 的 HTTP 请求头。

    参数：
    key_config (dict[str, Any]): key 配置字典。

    返回值：
    dict[str, str]: 请求头字典。
    """
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    api_key = str(key_config.get("subgraph_api_key") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def probe_subgraph_url(url: str, headers: dict[str, str], timeout_seconds: int) -> tuple[bool, str]:
    """
    功能：
    对单个 Subgraph URL 做轻量探针检查，判断是否可达且返回合法 GraphQL 数据。

    参数：
    url (str): Subgraph GraphQL 地址。
    headers (dict[str, str]): 请求头。
    timeout_seconds (int): 超时秒数。

    返回值：
    tuple[bool, str]: (是否可用, 描述信息)。
    """
    query = "{ _meta { block { number } } }"
    payload = {"query": query, "variables": {}}
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=timeout_seconds)
        response.raise_for_status()
        body = response.json()
        if body.get("errors"):
            return False, f"errors={body['errors']}"
        block = ((body.get("data") or {}).get("_meta") or {}).get("block") or {}
        block_num = int(block.get("number", -1))
        if block_num < 0:
            return False, "missing _meta.block.number"
        return True, f"ok block={block_num}"
    except Exception as exc:
        return False, str(exc)


def query_subgraph_agents_by_dids(
    subgraph_urls: list[str],
    headers: dict[str, str],
    dids: list[str],
    timeout_seconds: int = 20,
    request_retries: int = 3,
    retry_backoff_seconds: float = 2.0,
) -> dict[str, dict[str, Any]]:
    """
    功能：
    从 Subgraph 查询指定 DID 列表对应的 Agent 记录。

    参数：
    subgraph_urls (list[str]): Subgraph GraphQL 候选地址列表。
    headers (dict[str, str]): 请求头。
    dids (list[str]): 目标 DID 列表。
    timeout_seconds (int): 请求超时秒数。
    request_retries (int): 单次查询重试次数。
    retry_backoff_seconds (float): 重试退避基础秒数。

    返回值：
    dict[str, dict[str, Any]]: DID 到 Agent 记录的映射字典。
    """
    if not dids:
        return {}

    query = """
query AgentsByDid($dids: [String!]) {
  agents(where: { did_in: $dids }) {
    id
    did
    cid
    initScore
    accumulatedPenalty
    lastMisconductTimestamp
    isRegistered
    slashed
    lastUpdatedBlock
  }
}
    """.strip()

    payload = {"query": query, "variables": {"dids": dids}}
    last_error: Exception | None = None
    for attempt in range(1, max(1, request_retries) + 1):
        for url in subgraph_urls:
            try:
                response = requests.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=timeout_seconds,
                )
                response.raise_for_status()
                body = response.json()
                if body.get("errors"):
                    raise RuntimeError(f"Subgraph 查询失败: {body['errors']}")
                agents = (body.get("data") or {}).get("agents") or []
                mapping: dict[str, dict[str, Any]] = {}
                for item in agents:
                    did = str(item.get("did", "") or "")
                    if did:
                        mapping[did] = item
                return mapping
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                last_error = exc
                emit_progress(f"Subgraph 节点失败: {url} -> {exc}")
                continue
        if attempt < max(1, request_retries):
            wait_for = retry_backoff_seconds * float(attempt)
            emit_progress(
                f"Subgraph 轮询重试 ({attempt}/{request_retries})，等待 {wait_for:.1f}s"
            )
            time.sleep(wait_for)
        else:
            break
    if last_error is None:
        raise RuntimeError("Subgraph 查询失败: 未知错误")
    raise last_error


def wait_subgraph_index(
    subgraph_urls: list[str],
    headers: dict[str, str],
    expected_holders: list[dict[str, Any]],
    timeout_seconds: int,
    poll_interval_seconds: int,
) -> tuple[bool, int, float, dict[str, dict[str, Any]]]:
    """
    功能：
    轮询 Subgraph，等待本次 Holder 数据被索引并可查询。

    参数：
    subgraph_urls (list[str]): Subgraph GraphQL 地址列表。
    headers (dict[str, str]): 请求头。
    expected_holders (list[dict[str, Any]]): 预期 Holder 信息列表。
    timeout_seconds (int): 等待超时时间（秒）。
    poll_interval_seconds (int): 轮询间隔（秒）。

    返回值：
    tuple[bool, int, float, dict[str, dict[str, Any]]]:
    (是否成功, 轮询次数, 总耗时秒数, 最后一次查询映射)。
    """
    started = time.time()
    polls = 0
    transient_errors = 0
    expected_dids = [str(item["did"]) for item in expected_holders]
    expected_cids = {str(item["did"]): str(item["cid"]) for item in expected_holders}
    expected_init_scores = {
        str(item["did"]): int(item.get("expected_init_score", 0))
        for item in expected_holders
    }
    expected_penalties = {
        str(item["did"]): int(item.get("expected_accumulated_penalty", 0))
        for item in expected_holders
    }
    expected_last_ts = {
        str(item["did"]): int(item.get("expected_last_misconduct_ts", 0))
        for item in expected_holders
    }
    expected_slashed = {
        str(item["did"]): bool(item.get("expected_slashed", False))
        for item in expected_holders
    }
    last_seen: dict[str, dict[str, Any]] = {}

    emit_progress(
        f"开始等待 Subgraph 收录，目标={len(expected_dids)}，超时={timeout_seconds}s，轮询间隔={poll_interval_seconds}s"
    )
    while time.time() - started <= timeout_seconds:
        polls += 1
        try:
            seen = query_subgraph_agents_by_dids(
                subgraph_urls=subgraph_urls,
                headers=headers,
                dids=expected_dids,
            )
            last_seen = seen
        except Exception as exc:
            transient_errors += 1
            elapsed = time.time() - started
            emit_progress(
                f"轮询#{polls} 遇到暂时错误(累计{transient_errors})，已等待 {elapsed:.1f}s: {exc}"
            )
            time.sleep(poll_interval_seconds)
            continue

        all_ready = True
        ready_count = 0
        for did in expected_dids:
            agent_item = seen.get(did)
            if not agent_item:
                all_ready = False
                break
            if str(agent_item.get("cid", "")) != expected_cids[did]:
                all_ready = False
                break
            if int(agent_item.get("initScore", 0) or 0) != expected_init_scores[did]:
                all_ready = False
                break
            if int(agent_item.get("accumulatedPenalty", 0) or 0) != expected_penalties[did]:
                all_ready = False
                break
            if int(agent_item.get("lastMisconductTimestamp", 0) or 0) != expected_last_ts[did]:
                all_ready = False
                break
            if bool(agent_item.get("slashed", False)) != expected_slashed[did]:
                all_ready = False
                break
            if not bool(agent_item.get("isRegistered", False)):
                all_ready = False
                break
            ready_count += 1
        if all_ready:
            emit_progress(f"Subgraph 收录完成，轮询次数={polls}")
            return True, polls, time.time() - started, seen
        elapsed = time.time() - started
        emit_progress(
            f"轮询#{polls} 未全部就绪，当前就绪 {ready_count}/{len(expected_dids)}，已等待 {elapsed:.1f}s"
        )
        time.sleep(poll_interval_seconds)
    emit_progress("Subgraph 收录等待超时")
    return False, polls, time.time() - started, last_seen


def run_sidecar_assertion(
    run_dir: Path,
    start_block: int,
    expected_holders: list[dict[str, Any]],
    first: int,
    max_pages: int,
    max_rounds: int,
    search_top_k: int,
    subgraph_url_override: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """
    功能：
    执行 Sidecar 同步与检索断言，验证本次账户可被命中。

    参数：
    run_dir (Path): 本次运行目录。
    start_block (int): 同步起始区块。
    expected_holders (list[dict[str, Any]]): 预期 Holder 信息列表。
    first (int): 同步分页大小。
    max_pages (int): 单轮最大分页。
    max_rounds (int): 最大同步轮次。
    search_top_k (int): 检索断言的 top_k。
    subgraph_url_override (str | None): Sidecar 同步使用的 Subgraph URL 覆盖值。

    返回值：
    tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    (同步统计列表, 检索断言统计列表)。
    """
    db_path = run_dir / "discovery_sidecar_state.db"
    vector_index = TimedVectorIndex(delegate=SimpleVectorIndex(), backend_name="simple_memory")
    state_store = SQLiteStateStore(str(db_path))
    state_store.init_db()

    sync_rows: list[dict[str, Any]] = []
    assert_rows: list[dict[str, Any]] = []
    vector_summary_rows: list[dict[str, Any]] = []

    if subgraph_url_override:
        from sidecar.adapters import subgraph_client

        subgraph_client.SUBGRAPH_URL = subgraph_url_override
        emit_progress(f"Sidecar 使用 Subgraph: {subgraph_url_override}")

    try:
        sync_orchestrator = SyncOrchestrator(
            state_store=state_store,
            default_start_block=max(0, int(start_block)),
            vector_index=vector_index,
        )
        sync_started = time.time()
        sync_retry_attempts = 5
        sync_retry_sleep_seconds = 5
        sync_results = []
        last_sync_error: Exception | None = None
        for attempt in range(1, sync_retry_attempts + 1):
            try:
                emit_progress(f"Sidecar 同步尝试 {attempt}/{sync_retry_attempts}")
                sync_results = sync_orchestrator.sync_until_caught_up(
                    first=first,
                    max_pages=max_pages,
                    max_rounds=max_rounds,
                )
                last_sync_error = None
                break
            except Exception as exc:
                last_sync_error = exc
                emit_progress(f"Sidecar 同步失败: {exc}")
                if attempt < sync_retry_attempts:
                    time.sleep(sync_retry_sleep_seconds * attempt)
        if last_sync_error is not None:
            raise last_sync_error

        sync_elapsed = time.time() - sync_started

        for index, row in enumerate(sync_results):
            sync_rows.append(
                {
                    "sync_round": index + 1,
                    "from_block": row.from_block,
                    "to_block": row.to_block,
                    "fetched_count": row.fetched_count,
                    "written_count": row.written_count,
                    "rescored_count": row.rescored_count,
                    "reached_page_limit": row.reached_page_limit,
                    "sync_elapsed_seconds": sync_elapsed,
                }
            )
        emit_progress(f"Sidecar 同步完成，轮次数={len(sync_results)}，耗时={sync_elapsed:.2f}s")

        discovery_service = DiscoveryService(
            state_store=state_store,
            vector_index=vector_index,
        )
        for holder in expected_holders:
            query_text = str(holder["query_text"])
            target_address = str(holder["admin_address"]).lower()
            emit_progress(f"执行检索断言: query={query_text}")
            query_started = time.time()
            results = discovery_service.search(query=query_text, top_k=max(1, int(search_top_k)))
            query_ms = (time.time() - query_started) * 1000

            found = False
            rank = -1
            for idx, item in enumerate(results):
                if str(item.agent_address).lower() == target_address:
                    found = True
                    rank = idx + 1
                    break
            assert_rows.append(
                {
                    "query_text": query_text,
                    "target_agent": target_address,
                    "found": found,
                    "rank": rank,
                    "query_latency_ms": query_ms,
                    "vector_match_latency_ms": vector_index.get_last_query_latency_ms(),
                    "vector_result_count": vector_index.get_last_query_result_count(),
                    "vector_backend": vector_index.get_backend_name(),
                    "result_count": len(results),
                }
            )
        vector_summary_rows = vector_index.build_summary_rows()
    finally:
        state_store.close()
    return sync_rows, assert_rows, vector_summary_rows


def run_discovery_flow(
    config: dict[str, Any],
    key_config: dict[str, Any],
    root_key_config: dict[str, Any] | None,
    run_dir: Path,
    chain_tx_metrics: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    功能：
    执行发现闭环：metadata 生成、链上注册、Subgraph 等待、Sidecar 命中断言。

    参数：
    config (dict[str, Any]): 全流程配置字典。
    key_config (dict[str, Any]): agents_4_key 配置字典。
    root_key_config (dict[str, Any] | None): 根配置字典（用于获取 master 自动补币）。
    run_dir (Path): 本次运行目录。
    chain_tx_metrics (list[dict[str, Any]]): 链上指标列表（就地追加）。

    返回值：
    dict[str, Any]: 发现阶段的结果字典与统计指标。
    """
    discovery_cfg = dict(config.get("discovery", {}))
    accounts = key_config.get("accounts", {})
    if not isinstance(accounts, dict):
        raise ValueError("发现阶段需要有效 accounts 配置")

    api_url = str(key_config.get("api_url") or "").strip()
    if not api_url:
        raise ValueError("agents_4_key.json 缺少 api_url")
    w3 = Web3(Web3.HTTPProvider(api_url))
    if not w3.is_connected():
        raise RuntimeError(f"发现阶段 RPC 连接失败: {api_url}")

    registry_address = Web3.to_checksum_address(
        str(discovery_cfg.get("registry_address", "0x28249C2F09eF3196c1B42a0110dDD02D3B2b59B7"))
    )
    stake_eth = float(discovery_cfg.get("register_stake_eth", 0.01))

    registry = w3.eth.contract(address=registry_address, abi=AGENT_REGISTRY_V1_ABI)
    master_info = None
    if isinstance(root_key_config, dict):
        master_info = (root_key_config.get("accounts") or {}).get("master")
    if not master_info:
        master_info = (key_config.get("accounts") or {}).get("master")

    holder_admin_roles = ["agent_a_admin", "agent_b_admin"]
    holder_ports = [5000, 5001]

    holders: list[dict[str, Any]] = []
    case_assertions: list[dict[str, Any]] = []
    block_numbers: list[int] = []
    action_flags: list[str] = []
    cid_io_rows: list[dict[str, Any]] = []

    for idx, role in enumerate(holder_admin_roles):
        admin = accounts.get(role)
        if not isinstance(admin, dict):
            raise ValueError(f"发现阶段缺少角色账户: {role}")
        admin_addr = str(admin["address"])
        did = to_did(admin_addr)
        addr_suffix = admin_addr.lower().replace("0x", "")[-8:]
        query_keyword = f"fullflow-keyword-{role}-{addr_suffix}"
        metadata = generate_agent_metadata(
            agent_did=did,
            admin_address=admin_addr,
            service_name=f"Fullflow Holder {idx + 1}",
            service_summary=f"用于 fullflow 全流程验证的 holder 服务 {role}",
            service_domain="fullflow-test",
            endpoint_url=f"http://localhost:{holder_ports[idx]}",
            capability_name="Audit Response",
            capability_description="支持认证、探测、上下文一致性响应",
            searchable_keywords=[query_keyword, "fullflow", "agentdid"],
            vector_text=f"{query_keyword} fullflow agentdid holder {role}",
        )
        metadata = make_metadata_deterministic(metadata=metadata, stable_tag=role)
        cid, cid_upload_seconds, cid_upload_bytes = cache_metadata_as_local_cid(metadata)
        cid_io_rows.append(
            {
                "metric_type": "cid_io",
                "phase": "discovery",
                "cid_scope": "metadata",
                "cid_role": role,
                "cid": cid,
                "io_direction": "upload",
                "io_backend": "local_cache",
                "io_seconds": cid_upload_seconds,
                "payload_bytes": cid_upload_bytes,
            }
        )
        loaded_payload, cid_download_seconds, cid_download_bytes = load_local_cid_payload(cid)
        cid_io_rows.append(
            {
                "metric_type": "cid_io",
                "phase": "discovery",
                "cid_scope": "metadata",
                "cid_role": role,
                "cid": cid,
                "io_direction": "download",
                "io_backend": "local_cache",
                "io_seconds": cid_download_seconds,
                "payload_bytes": cid_download_bytes,
            }
        )
        cid_roundtrip_ok = loaded_payload == metadata
        case_assertions.append(
            build_case_assertion(
                case_id=f"discovery_cid_roundtrip_{role}",
                capability_id="discovery.cid_roundtrip",
                expected="CID payload roundtrip should equal metadata",
                actual=(
                    f"ok={cid_roundtrip_ok} "
                    f"upload_s={cid_upload_seconds:.6f} download_s={cid_download_seconds:.6f} "
                    f"bytes={cid_upload_bytes}"
                ),
                passed=cid_roundtrip_ok,
                error="" if cid_roundtrip_ok else "CID roundtrip payload mismatch",
            )
        )
        if not cid_roundtrip_ok:
            raise AssertionError(f"CID roundtrip 校验失败: role={role}")
        emit_progress(f"准备注册/更新 {role}，cid={cid}")

        block_number, action = register_or_update_agent(
            w3=w3,
            registry_contract=registry,
            admin_address=admin_addr,
            admin_private_key=str(admin["private_key"]),
            did=did,
            cid=cid,
            stake_eth=stake_eth,
            chain_tx_metrics=chain_tx_metrics,
            actor_role=role,
            funder_info=master_info if isinstance(master_info, dict) else None,
            balance_reserve_eth=float(discovery_cfg.get("balance_reserve_eth", 0.002)),
            case_id=f"discovery_register_update_{role}",
        )
        state_tuple = registry.functions.agents(Web3.to_checksum_address(admin_addr)).call()
        block_numbers.append(block_number)
        action_flags.append(action)
        holders.append(
            {
                "role": role,
                "admin_address": admin_addr,
                "did": did,
                "cid": cid,
                "query_text": query_keyword,
                "port": holder_ports[idx],
                "expected_init_score": int(state_tuple[2]),
                "expected_accumulated_penalty": int(state_tuple[3]),
                "expected_last_misconduct_ts": int(state_tuple[4]),
                "expected_slashed": bool(state_tuple[6]),
            }
        )
        emit_progress(f"{role} 完成链上更新，block={block_number}")

    bind_current = bool(config.get("discovery_bind_current", True))
    subgraph_ok = True
    poll_count = 0
    wait_seconds = 0.0
    subgraph_seen: dict[str, dict[str, Any]] = {}
    selected_subgraph_url = str(
        key_config.get("subgraph_url")
        or "https://api.studio.thegraph.com/query/1740029/agent-registry-sepolia/version/latest"
    )

    if bind_current:
        subgraph_urls = unique_subgraph_urls(
            primary_url=selected_subgraph_url,
            fallback_urls=list(discovery_cfg.get("subgraph_url_pool", [])),
        )
        emit_progress(f"Subgraph 候选节点数: {len(subgraph_urls)}")
        headers = build_subgraph_headers(key_config)
        healthy_urls: list[str] = []
        for url in subgraph_urls:
            ok, detail = probe_subgraph_url(
                url=url,
                headers=headers,
                timeout_seconds=int(discovery_cfg.get("subgraph_probe_timeout_seconds", 10)),
            )
            emit_progress(f"子图预检: {url} -> {detail}")
            if ok:
                healthy_urls.append(url)
        if healthy_urls:
            selected_subgraph_url = healthy_urls[0]
            emit_progress(f"选择可用子图节点: {selected_subgraph_url}")
        else:
            emit_progress("未探测到健康子图节点，进入轮询重试流程")

        subgraph_ok, poll_count, wait_seconds, subgraph_seen = wait_subgraph_index(
            subgraph_urls=healthy_urls if healthy_urls else subgraph_urls,
            headers=headers,
            expected_holders=holders,
            timeout_seconds=int(discovery_cfg.get("wait_timeout_seconds", 600)),
            poll_interval_seconds=int(discovery_cfg.get("poll_interval_seconds", 15)),
        )
        if not subgraph_ok:
            raise TimeoutError("等待 Subgraph 收录本次账户超时")

    default_start_block = int(discovery_cfg.get("sidecar_start_block", 10360859))
    if block_numbers and any(item != "noop" for item in action_flags):
        start_block = max(0, min(block_numbers) - 2)
    else:
        start_block = default_start_block
    sync_rows, assert_rows, vector_summary_rows = run_sidecar_assertion(
        run_dir=run_dir,
        start_block=start_block,
        expected_holders=holders,
        first=int(discovery_cfg.get("sidecar_first", 200)),
        max_pages=int(discovery_cfg.get("sidecar_max_pages", 50)),
        max_rounds=int(discovery_cfg.get("sidecar_max_rounds", 10)),
        search_top_k=int(discovery_cfg.get("sidecar_search_top_k", 20)),
        subgraph_url_override=selected_subgraph_url,
    )

    discovery_metrics: list[dict[str, Any]] = []
    discovery_metrics.append(
        {
            "metric_type": "subgraph_wait",
            "success": subgraph_ok,
            "poll_count": poll_count,
            "wait_seconds": wait_seconds,
            "bind_current": bind_current,
            "selected_subgraph_url": selected_subgraph_url,
        }
    )
    for row in sync_rows:
        metric = dict(row)
        metric["metric_type"] = "sidecar_sync"
        discovery_metrics.append(metric)
    for row in assert_rows:
        metric = dict(row)
        metric["metric_type"] = "search_assertion"
        discovery_metrics.append(metric)
    discovery_metrics.extend(cid_io_rows)
    for row in vector_summary_rows:
        metric = dict(row)
        metric["metric_type"] = "vector_index_summary"
        discovery_metrics.append(metric)

    vector_query_values: list[float] = []
    for row in assert_rows:
        try:
            value = float(row.get("vector_match_latency_ms", 0.0))
        except (TypeError, ValueError):
            value = -1.0
        if value >= 0:
            vector_query_values.append(value)
    if assert_rows:
        vector_timing_ok = len(vector_query_values) == len(assert_rows)
        vector_avg_ms = sum(vector_query_values) / max(1, len(vector_query_values))
        case_assertions.append(
            build_case_assertion(
                case_id="discovery_vector_match_timing_collected",
                capability_id="discovery.vector_match_timing",
                expected="all discovery queries contain vector_match_latency_ms",
                actual=f"queries={len(assert_rows)} avg_vector_match_ms={vector_avg_ms:.4f}",
                passed=vector_timing_ok,
                error="" if vector_timing_ok else "missing vector timing field",
            )
        )

    if bind_current:
        for holder in holders:
            did = str(holder["did"])
            item = subgraph_seen.get(did, {})
            expected = {
                "cid": holder["cid"],
                "initScore": int(holder["expected_init_score"]),
                "accumulatedPenalty": int(holder["expected_accumulated_penalty"]),
                "lastMisconductTimestamp": int(holder["expected_last_misconduct_ts"]),
                "slashed": bool(holder["expected_slashed"]),
                "isRegistered": True,
            }
            actual = {
                "cid": str(item.get("cid", "")),
                "initScore": int(item.get("initScore", 0) or 0),
                "accumulatedPenalty": int(item.get("accumulatedPenalty", 0) or 0),
                "lastMisconductTimestamp": int(item.get("lastMisconductTimestamp", 0) or 0),
                "slashed": bool(item.get("slashed", False)),
                "isRegistered": bool(item.get("isRegistered", False)),
            }
            passed = expected == actual
            case_assertions.append(
                build_case_assertion(
                    case_id=f"discovery_subgraph_state_{holder['role']}",
                    capability_id="discovery.subgraph_state_consistency",
                    expected=json.dumps(expected, ensure_ascii=False, sort_keys=True),
                    actual=json.dumps(actual, ensure_ascii=False, sort_keys=True),
                    passed=passed,
                    error="" if passed else "Subgraph 状态字段与链上预期不一致",
                )
            )
            if not passed:
                raise AssertionError(
                    f"Subgraph 状态不一致: role={holder['role']} expected={expected} actual={actual}"
                )

    for row in assert_rows:
        case_assertions.append(
            build_case_assertion(
                case_id=f"discovery_sidecar_hit_{str(row.get('query_text', 'unknown'))}",
                capability_id="discovery.sidecar_search_hit",
                expected="found=true",
                actual=f"found={bool(row.get('found'))}, rank={int(row.get('rank', -1))}",
                passed=bool(row.get("found")),
                error="" if bool(row.get("found")) else "Sidecar 未命中目标 Agent",
            )
        )
        if not bool(row["found"]):
            raise AssertionError(
                f"Sidecar 检索未命中本次账户: query={row['query_text']} target={row['target_agent']}"
            )

    return {
        "holders": holders,
        "start_block": start_block,
        "discovery_metrics": discovery_metrics,
        "case_assertions": case_assertions,
    }

