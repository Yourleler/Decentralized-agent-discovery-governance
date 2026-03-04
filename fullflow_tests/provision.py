from __future__ import annotations

import copy
import json
from pathlib import Path
import time
from typing import Any

from web3 import Web3

from infrastructure.utils import REGISTRY_ABI, REGISTRY_ADDRESS


def emit_progress(message: str) -> None:
    """
    功能：
    输出 provision 阶段进度日志。

    参数：
    message (str): 进度消息文本。

    返回值：
    None: 仅打印日志，不返回数据。
    """
    ts = time.strftime("%H:%M:%S")
    print(f"[fullflow][{ts}][PROVISION] {message}", flush=True)


def to_did(address: str) -> str:
    """
    功能：
    将以太坊地址转换为 did:ethr:sepolia 格式 DID。

    参数：
    address (str): 以太坊地址字符串。

    返回值：
    str: DID 字符串。
    """
    return f"did:ethr:sepolia:{address}"


def connect_web3(api_url: str) -> Web3:
    """
    功能：
    创建并校验 Web3 HTTP 连接。

    参数：
    api_url (str): RPC 节点地址。

    返回值：
    Web3: 可用的 Web3 实例。
    """
    w3 = Web3(Web3.HTTPProvider(api_url))
    if not w3.is_connected():
        raise RuntimeError(f"RPC 连接失败: {api_url}")
    return w3


def required_roles(agent_names: list[str]) -> list[str]:
    """
    功能：
    按 Agent 名单生成最小必需角色列表。

    参数：
    agent_names (list[str]): Agent 名称列表，例如 agent_a。

    返回值：
    list[str]: 必需角色名称列表。
    """
    roles = ["issuer"]
    for name in agent_names:
        roles.append(f"{name}_admin")
        roles.append(f"{name}_op")
    return roles


def calculate_tx_cost(receipt: dict[str, Any], tx: dict[str, Any]) -> tuple[int, float]:
    """
    功能：
    基于回执与交易参数计算 gasPrice 与 ETH 成本。

    参数：
    receipt (dict[str, Any]): 交易回执字典。
    tx (dict[str, Any]): 原始交易字典。

    返回值：
    tuple[int, float]: (gas_price_wei, cost_eth)。
    """
    gas_used = int(receipt.get("gasUsed", 0))
    gas_price_wei = int(receipt.get("effectiveGasPrice", tx.get("gasPrice", 0)))
    cost_eth = float(Web3.from_wei(gas_used * gas_price_wei, "ether"))
    return gas_price_wei, cost_eth


def build_tx_metric(
    category: str,
    actor: str,
    tx_hash: str,
    receipt: dict[str, Any],
    tx: dict[str, Any],
    latency_seconds: float,
    note: str = "",
) -> dict[str, Any]:
    """
    功能：
    构造统一链上交易指标记录。

    参数：
    category (str): 交易分类，例如 provision_fund。
    actor (str): 交易执行者标签。
    tx_hash (str): 交易哈希。
    receipt (dict[str, Any]): 交易回执。
    tx (dict[str, Any]): 原始交易字典。
    latency_seconds (float): 广播到确认的耗时（秒）。
    note (str): 附加说明文本。

    返回值：
    dict[str, Any]: 统一结构的交易指标字典。
    """
    gas_price_wei, cost_eth = calculate_tx_cost(receipt, tx)
    return {
        "category": category,
        "actor": actor,
        "tx_hash": tx_hash,
        "block_number": int(receipt.get("blockNumber", 0)),
        "gas_used": int(receipt.get("gasUsed", 0)),
        "gas_price_wei": gas_price_wei,
        "cost_eth": cost_eth,
        "latency_seconds": float(latency_seconds),
        "status": int(receipt.get("status", 0)),
        "note": note,
    }


def validate_reusable_key_file(
    key_config: dict[str, Any],
    w3: Web3,
    agent_names: list[str],
    min_balance_eth: float,
    check_balance: bool = True,
) -> tuple[bool, str]:
    """
    功能：
    检查现有 agents_4_key 配置是否可复用于 fullflow 测试。

    参数：
    key_config (dict[str, Any]): 待验证的 key 配置字典。
    w3 (Web3): Web3 连接实例。
    agent_names (list[str]): 目标 Agent 名称列表。
    min_balance_eth (float): Admin 地址最低余额阈值（ETH）。
    check_balance (bool): 是否启用余额阈值检查。

    返回值：
    tuple[bool, str]: (是否可复用, 失败原因)。
    """
    accounts = key_config.get("accounts", {})
    if not isinstance(accounts, dict):
        return False, "accounts 字段不存在或格式错误"

    for role in required_roles(agent_names):
        if role not in accounts:
            return False, f"缺少角色: {role}"
        entry = accounts[role]
        if not isinstance(entry, dict):
            return False, f"角色结构非法: {role}"
        if not entry.get("address") or not entry.get("private_key"):
            return False, f"角色缺少 address/private_key: {role}"

    if check_balance:
        threshold_wei = w3.to_wei(min_balance_eth, "ether")
        for agent_name in agent_names:
            role = f"{agent_name}_admin"
            address = accounts[role]["address"]
            balance = w3.eth.get_balance(address)
            if balance < threshold_wei:
                return False, f"余额不足: {role} {balance} wei < {threshold_wei} wei"
    return True, ""


def generate_agent_accounts(
    w3: Web3,
    agent_names: list[str],
) -> list[dict[str, Any]]:
    """
    功能：
    生成 Agent Admin/Op 账户对。

    参数：
    w3 (Web3): Web3 实例，用于调用 account.create。
    agent_names (list[str]): Agent 名称列表。

    返回值：
    list[dict[str, Any]]: 每个 Agent 的账户数据列表。
    """
    agents: list[dict[str, Any]] = []
    for name in agent_names:
        now_seed = f"{name}_{time.time()}"
        admin_acct = w3.eth.account.create(extra_entropy=f"{now_seed}_admin")
        op_acct = w3.eth.account.create(extra_entropy=f"{now_seed}_op")
        agents.append(
            {
                "name": name,
                "admin": {"address": admin_acct.address, "private_key": admin_acct.key.hex()},
                "op": {"address": op_acct.address, "private_key": op_acct.key.hex()},
            }
        )
    return agents


def send_and_wait(
    w3: Web3,
    tx: dict[str, Any],
    private_key: str,
    timeout_seconds: int = 300,
) -> tuple[str, dict[str, Any], float]:
    """
    功能：
    签名并发送交易，等待回执并返回耗时。

    参数：
    w3 (Web3): Web3 实例。
    tx (dict[str, Any]): 原始交易字典。
    private_key (str): 发送方私钥。
    timeout_seconds (int): 等待回执超时时间。

    返回值：
    tuple[str, dict[str, Any], float]: (tx_hash_hex, receipt_dict, latency_seconds)。
    """
    start_ts = time.time()
    signed = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout_seconds)
    latency = time.time() - start_ts
    return w3.to_hex(tx_hash), dict(receipt), latency


def fund_admin_accounts(
    w3: Web3,
    funder: dict[str, str],
    agents: list[dict[str, Any]],
    amount_eth: float,
    chain_tx_metrics: list[dict[str, Any]],
) -> None:
    """
    功能：
    使用主账户向每个 Agent Admin 地址转入测试 ETH。

    参数：
    w3 (Web3): Web3 实例。
    funder (dict[str, str]): 出资账户信息，包含 address/private_key。
    agents (list[dict[str, Any]]): Agent 账户列表。
    amount_eth (float): 单个 Admin 充值金额（ETH）。
    chain_tx_metrics (list[dict[str, Any]]): 链上指标列表（就地追加）。

    返回值：
    None: 函数仅执行转账并写入指标。
    """
    funder_addr = funder["address"]
    funder_pk = funder["private_key"]
    chain_id = int(w3.eth.chain_id)
    nonce = int(w3.eth.get_transaction_count(funder_addr, "pending"))
    gas_price = int(w3.eth.gas_price * 1.2)

    for idx, agent in enumerate(agents):
        tx = {
            "nonce": nonce + idx,
            "to": agent["admin"]["address"],
            "value": w3.to_wei(amount_eth, "ether"),
            "gas": 21000,
            "gasPrice": gas_price,
            "chainId": chain_id,
        }
        tx_hash, receipt, latency = send_and_wait(w3, tx, funder_pk)
        chain_tx_metrics.append(
            build_tx_metric(
                category="provision_fund",
                actor="master",
                tx_hash=tx_hash,
                receipt=receipt,
                tx=tx,
                latency_seconds=latency,
                note=f"to={agent['name']}_admin",
            )
        )


def register_did_implicit(
    w3: Web3,
    agents: list[dict[str, Any]],
    chain_tx_metrics: list[dict[str, Any]],
) -> None:
    """
    功能：
    通过 0 ETH 自转账方式为每个 Admin 地址触发 DID 隐式注册。

    参数：
    w3 (Web3): Web3 实例。
    agents (list[dict[str, Any]]): Agent 账户列表。
    chain_tx_metrics (list[dict[str, Any]]): 链上指标列表（就地追加）。

    返回值：
    None: 函数仅执行交易并写入指标。
    """
    chain_id = int(w3.eth.chain_id)
    gas_price = int(w3.eth.gas_price * 1.4)
    for agent in agents:
        admin_addr = agent["admin"]["address"]
        admin_pk = agent["admin"]["private_key"]
        nonce = int(w3.eth.get_transaction_count(admin_addr, "pending"))
        tx = {
            "nonce": nonce,
            "to": admin_addr,
            "value": 0,
            "gas": 21000,
            "gasPrice": gas_price,
            "chainId": chain_id,
        }
        tx_hash, receipt, latency = send_and_wait(w3, tx, admin_pk)
        chain_tx_metrics.append(
            build_tx_metric(
                category="provision_did_register",
                actor=f"{agent['name']}_admin",
                tx_hash=tx_hash,
                receipt=receipt,
                tx=tx,
                latency_seconds=latency,
                note="implicit_did_registration",
            )
        )


def add_delegate_authorization(
    w3: Web3,
    agents: list[dict[str, Any]],
    chain_tx_metrics: list[dict[str, Any]],
) -> None:
    """
    功能：
    调用 ethr-did-registry setAttribute 为每个 Admin 授权对应 Op Delegate。

    参数：
    w3 (Web3): Web3 实例。
    agents (list[dict[str, Any]]): Agent 账户列表。
    chain_tx_metrics (list[dict[str, Any]]): 链上指标列表（就地追加）。

    返回值：
    None: 函数仅执行交易并写入指标。
    """
    contract = w3.eth.contract(address=REGISTRY_ADDRESS, abi=REGISTRY_ABI)
    key_name = "did/pub/Secp256k1/sigAuth/hex".encode("utf-8").ljust(32, b"\0")
    validity = 365 * 24 * 60 * 60
    chain_id = int(w3.eth.chain_id)
    gas_price = int(w3.eth.gas_price * 1.4)

    for agent in agents:
        admin_addr = agent["admin"]["address"]
        admin_pk = agent["admin"]["private_key"]
        op_addr = agent["op"]["address"]
        nonce = int(w3.eth.get_transaction_count(admin_addr, "pending"))
        tx = contract.functions.setAttribute(
            admin_addr,
            key_name,
            bytes.fromhex(op_addr[2:]),
            validity,
        ).build_transaction(
            {
                "chainId": chain_id,
                "gas": 200000,
                "gasPrice": gas_price,
                "nonce": nonce,
            }
        )
        tx_hash, receipt, latency = send_and_wait(w3, tx, admin_pk)
        chain_tx_metrics.append(
            build_tx_metric(
                category="provision_add_delegate",
                actor=f"{agent['name']}_admin",
                tx_hash=tx_hash,
                receipt=receipt,
                tx=tx,
                latency_seconds=latency,
                note=f"delegate={op_addr}",
            )
        )


def compose_agents_key_config(
    root_key_config: dict[str, Any],
    agents: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    功能：
    基于根配置与新生成账户构造 agents_4_key.json 内容。

    参数：
    root_key_config (dict[str, Any]): config/key.json 的配置字典。
    agents (list[dict[str, Any]]): 新生成的 Agent 账户列表。

    返回值：
    dict[str, Any]: 可写入 agents_4_key.json 的完整配置字典。
    """
    output = copy.deepcopy(root_key_config)
    output_accounts: dict[str, Any] = {}

    if "master" in root_key_config.get("accounts", {}):
        output_accounts["master"] = copy.deepcopy(root_key_config["accounts"]["master"])
    if "issuer" in root_key_config.get("accounts", {}):
        output_accounts["issuer"] = copy.deepcopy(root_key_config["accounts"]["issuer"])

    for agent in agents:
        output_accounts[f"{agent['name']}_admin"] = copy.deepcopy(agent["admin"])
        output_accounts[f"{agent['name']}_op"] = copy.deepcopy(agent["op"])
    output["accounts"] = output_accounts
    return output


def ensure_accounts(
    config: dict[str, Any],
    root_key_config: dict[str, Any],
    run_dir: Path,
    chain_tx_metrics: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    功能：
    按 mixed/reuse/fresh 策略准备 fullflow 所需账户配置。

    参数：
    config (dict[str, Any]): 全流程运行配置。
    root_key_config (dict[str, Any]): 根配置（config/key.json）字典。
    run_dir (Path): 本次运行目录。
    chain_tx_metrics (list[dict[str, Any]]): 链上指标列表（就地追加）。

    返回值：
    dict[str, Any]: 账户准备结果，含 key_path、key_config 与策略信息。
    """
    provision_cfg = dict(config.get("provision", {}))
    discovery_cfg = dict(config.get("discovery", {}))
    strategy = str(config.get("account_strategy", "mixed")).lower()
    if strategy not in {"mixed", "reuse", "fresh"}:
        raise ValueError(f"不支持的 account_strategy: {strategy}")

    api_url = str(root_key_config.get("api_url", "")).strip()
    if not api_url:
        raise ValueError("config/key.json 缺少 api_url")
    w3 = connect_web3(api_url)

    agent_names = provision_cfg.get("agent_names", ["agent_a", "agent_b", "agent_c", "agent_d"])
    if not isinstance(agent_names, list) or not agent_names:
        raise ValueError("provision.agent_names 必须是非空列表")

    min_balance_eth_cfg = float(provision_cfg.get("min_balance_eth", 0.002))
    fund_amount_eth_cfg = float(provision_cfg.get("fund_amount_eth", 0.005))
    register_stake_eth = float(discovery_cfg.get("register_stake_eth", 0.01))
    register_gas_reserve_eth = float(discovery_cfg.get("register_gas_reserve_eth", 0.003))
    post_register_buffer_eth = float(provision_cfg.get("post_register_buffer_eth", 0.002))

    required_admin_balance_eth = register_stake_eth + register_gas_reserve_eth
    min_balance_eth = max(min_balance_eth_cfg, required_admin_balance_eth)
    fund_amount_eth = max(
        fund_amount_eth_cfg,
        required_admin_balance_eth + post_register_buffer_eth,
    )

    key_path = Path("config/agents_4_key.json").resolve()
    reuse_allowed = strategy in {"mixed", "reuse"}
    fresh_required = strategy == "fresh"
    reuse_check_balance = strategy == "reuse"

    if reuse_allowed and key_path.exists() and not fresh_required:
        with key_path.open("r", encoding="utf-8") as f:
            key_config = json.load(f)
        emit_progress("检测到现有 agents_4_key.json，开始复用校验")
        ok, reason = validate_reusable_key_file(
            key_config=key_config,
            w3=w3,
            agent_names=agent_names,
            min_balance_eth=min_balance_eth,
            check_balance=reuse_check_balance,
        )
        if ok:
            emit_progress("复用校验通过，直接使用现有账户")
            return {
                "strategy_used": "reuse",
                "key_path": str(key_path),
                "key_config": key_config,
                "note": "复用现有 agents_4_key.json",
            }
        if strategy == "reuse":
            raise RuntimeError(f"reuse 策略校验失败: {reason}")
        emit_progress(f"复用校验失败，回退新建账户: {reason}")

    master_role = str(provision_cfg.get("funder_account", "master"))
    master_info = root_key_config.get("accounts", {}).get(master_role)
    if not isinstance(master_info, dict):
        raise ValueError(f"config/key.json 缺少出资账户: {master_role}")

    agents = generate_agent_accounts(w3=w3, agent_names=agent_names)
    emit_progress(f"已生成 {len(agents)} 组账户，开始主账户打币")
    fund_admin_accounts(
        w3=w3,
        funder=master_info,
        agents=agents,
        amount_eth=fund_amount_eth,
        chain_tx_metrics=chain_tx_metrics,
    )
    emit_progress("主账户打币完成，开始隐式 DID 注册")
    register_did_implicit(w3=w3, agents=agents, chain_tx_metrics=chain_tx_metrics)
    emit_progress("隐式 DID 注册完成，开始 Delegate 授权")
    add_delegate_authorization(w3=w3, agents=agents, chain_tx_metrics=chain_tx_metrics)
    emit_progress("Delegate 授权完成，写入 agents_4_key.json")

    key_config = compose_agents_key_config(root_key_config=root_key_config, agents=agents)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    with key_path.open("w", encoding="utf-8") as f:
        json.dump(key_config, f, ensure_ascii=False, indent=2)

    snapshot_path = run_dir / "agents_4_key.snapshot.json"
    with snapshot_path.open("w", encoding="utf-8") as f:
        json.dump(key_config, f, ensure_ascii=False, indent=2)

    return {
        "strategy_used": "fresh",
        "key_path": str(key_path),
        "key_config": key_config,
        "note": "已新建账户并写入 agents_4_key.json",
    }
