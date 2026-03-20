from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any

from web3 import Web3


def emit_progress(message: str) -> None:
    """
    功能：
    输出 governance 阶段进度日志。

    参数：
    message (str): 进度消息文本。

    返回值：
    None: 仅打印日志，不返回数据。
    """
    ts = time.strftime("%H:%M:%S")
    print(f"[fullflow][{ts}][GOVERNANCE] {message}", flush=True)


def build_case_assertion(
    case_id: str,
    capability_id: str,
    expected: str,
    actual: str,
    passed: bool,
    phase: str = "governance",
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
    phase (str): 阶段名称。
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


GOVERNANCE_ABI: list[dict[str, Any]] = [
    {
        "inputs": [
            {"internalType": "address", "name": "_targetAgent", "type": "address"},
            {"internalType": "string", "name": "_evidenceCid", "type": "string"},
        ],
        "name": "reportMisbehavior",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


def cache_evidence_to_local_ipfs(evidence_payload: dict[str, Any]) -> tuple[str, float, int]:
    """
    功能：
    将治理证据 JSON 写入本地 .ipfs_cache 并返回本地 CID 字符串。

    参数：
    evidence_payload (dict[str, Any]): 证据对象。

    返回值：
    str: 本地 CID 字符串。
    """
    raw = json.dumps(
        evidence_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    cid = f"local-evidence-{digest[:40]}"
    cache_dir = Path(".ipfs_cache").resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    (cache_dir / cid).write_bytes(raw)
    elapsed = time.perf_counter() - started
    return cid, elapsed, len(raw)


def load_local_evidence_cid(cid: str) -> tuple[dict[str, Any], float, int]:
    """
    从本地 .ipfs_cache 读取治理证据 CID，用于统计下载耗时。
    """
    cache_path = Path(".ipfs_cache").resolve() / cid
    started = time.perf_counter()
    raw = cache_path.read_bytes()
    elapsed = time.perf_counter() - started
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"证据 CID 内容不是对象: {cid}")
    return payload, elapsed, len(raw)


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
    构造治理阶段链上交易指标记录。

    参数：
    category (str): 指标分类。
    actor (str): 执行者标签。
    tx_hash (str): 交易哈希。
    receipt (dict[str, Any]): 交易回执。
    gas_price_wei (int): 广播交易时 gasPrice。
    latency_seconds (float): 广播到确认耗时。
    note (str): 附加说明。

    返回值：
    dict[str, Any]: 统一结构交易指标字典。
    """
    gas_used = int(receipt.get("gasUsed", 0))
    effective = int(receipt.get("effectiveGasPrice", gas_price_wei))
    cost_eth = float(Web3.from_wei(gas_used * effective, "ether"))
    return {
        "category": category,
        "phase": "governance",
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


def submit_sepolia_report(
    w3: Web3,
    registry_address: str,
    reporter_address: str,
    reporter_private_key: str,
    target_agent_address: str,
    evidence_cid: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    功能：
    在 Sepolia 合约上提交 reportMisbehavior 交易。

    参数：
    w3 (Web3): Web3 实例。
    registry_address (str): AgentRegistry_v1 合约地址。
    reporter_address (str): 举报者地址。
    reporter_private_key (str): 举报者私钥。
    target_agent_address (str): 被举报 Agent 地址。
    evidence_cid (str): 证据 CID。

    返回值：
    tuple[dict[str, Any], dict[str, Any]]:
    (治理指标行, 链上交易指标行)。
    """
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(registry_address),
        abi=GOVERNANCE_ABI,
    )
    nonce = int(w3.eth.get_transaction_count(reporter_address, "pending"))
    gas_price = int(w3.eth.gas_price * 1.1)
    chain_id = int(w3.eth.chain_id)

    tx = contract.functions.reportMisbehavior(
        Web3.to_checksum_address(target_agent_address),
        evidence_cid,
    ).build_transaction(
        {
            "chainId": chain_id,
            "nonce": nonce,
            "gas": 300000,
            "gasPrice": gas_price,
            "value": 0,
        }
    )

    started = time.time()
    signed = w3.eth.account.sign_transaction(tx, reporter_private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
    latency = time.time() - started

    tx_metric = build_tx_metric(
        category="governance_report_misbehavior",
        actor="master",
        tx_hash=w3.to_hex(tx_hash),
        receipt=dict(receipt),
        gas_price_wei=gas_price,
        latency_seconds=latency,
        note=f"target={target_agent_address} cid={evidence_cid}",
        case_id="governance_sepolia_report_misbehavior",
    )
    gov_metric = {
        "mode": "sepolia",
        "action": "reportMisbehavior",
        "target_agent": target_agent_address,
        "evidence_cid": evidence_cid,
        "tx_hash": w3.to_hex(tx_hash),
        "status": "passed" if int(receipt.get("status", 0)) == 1 else "failed",
        "latency_seconds": latency,
    }
    return gov_metric, tx_metric


def run_local_governance_script(script_path: str, cwd: Path) -> dict[str, Any]:
    """
    功能：
    执行本地 Hardhat 治理脚本并解析输出结果。

    参数：
    script_path (str): 本地治理脚本路径。
    cwd (Path): 执行工作目录。

    返回值：
    dict[str, Any]: 本地治理执行结果字典。
    """
    command = build_local_governance_command(script_path=script_path, cwd=cwd)
    started = time.time()
    proc = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    latency = time.time() - started

    result_line_prefix = "FULLFLOW_LOCAL_GOV_RESULT="
    parsed_payload: dict[str, Any] = {}
    for line in (proc.stdout or "").splitlines():
        if line.startswith(result_line_prefix):
            raw_json = line[len(result_line_prefix) :].strip()
            parsed_payload = json.loads(raw_json)
            break

    metric = {
        "mode": "local",
        "action": "report_slash_restore_freeze_unfreeze_appeal",
        "status": "passed" if proc.returncode == 0 else "failed",
        "latency_seconds": latency,
        "return_code": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-1000:],
        "stderr_tail": (proc.stderr or "")[-1000:],
        "payload": parsed_payload,
    }
    return metric


def validate_local_governance_payload(payload: dict[str, Any]) -> tuple[bool, str]:
    """
    功能：
    校验本地治理脚本返回 payload 是否包含关键治理动作结果。

    参数：
    payload (dict[str, Any]): 本地脚本输出 payload。

    返回值：
    tuple[bool, str]: (是否通过, 错误信息)。
    """
    required_bools = ["reportSubmitted", "freezeApplied", "unfreezeApplied", "appealSubmitted"]
    for key in required_bools:
        if not isinstance(payload.get(key), bool):
            return False, f"缺少或非法字段: {key}"
        if not bool(payload.get(key)):
            return False, f"关键动作未成功: {key}"
    if not isinstance(payload.get("afterSlash"), dict):
        return False, "缺少 afterSlash"
    if not isinstance(payload.get("afterRestore"), dict):
        return False, "缺少 afterRestore"
    if "accumulatedPenalty" not in payload["afterSlash"] or "accumulatedPenalty" not in payload["afterRestore"]:
        return False, "afterSlash/afterRestore 缺少 accumulatedPenalty"
    return True, ""


def build_local_governance_command(script_path: str, cwd: Path) -> list[str]:
    """
    功能：
    解析本地治理脚本执行命令，兼容 Windows 下 npx/npx.cmd 差异并提供 node+hardhat 兜底。

    参数：
    script_path (str): 本地治理脚本路径。
    cwd (Path): 执行工作目录。

    返回值：
    list[str]: 可直接传入 subprocess.run 的命令参数列表。
    """
    script_file = Path(script_path)
    if not script_file.is_absolute():
        script_file = (cwd / script_file).resolve()
    else:
        script_file = script_file.resolve()
    if not script_file.exists():
        raise FileNotFoundError(f"本地治理脚本不存在: {script_file}")

    # Windows 下直接执行 `npx` 可能触发 WinError 2，优先显式寻找 npx.cmd。
    npx_exec = shutil.which("npx.cmd") or shutil.which("npx")
    if npx_exec:
        return [npx_exec, "hardhat", "run", str(script_file)]

    node_exec = shutil.which("node")
    hardhat_cli = (cwd / "node_modules" / "hardhat" / "dist" / "src" / "cli.js").resolve()
    if node_exec and hardhat_cli.exists():
        return [node_exec, str(hardhat_cli), "run", str(script_file)]

    comspec = os.environ.get("ComSpec")
    if comspec:
        return [comspec, "/c", "npx", "hardhat", "run", str(script_file)]

    raise FileNotFoundError(
        "未找到可执行的 Hardhat 命令。请确认 Node.js 与 npx 已安装并在 PATH 中。"
    )


def run_governance_flow(
    config: dict[str, Any],
    root_key_config: dict[str, Any],
    discovery_result: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    run_dir: Path,
    chain_tx_metrics: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    功能：
    执行治理闭环，支持 Sepolia 举报与本地完整治理脚本。

    参数：
    config (dict[str, Any]): 全流程配置字典。
    root_key_config (dict[str, Any]): config/key.json 配置字典。
    discovery_result (dict[str, Any]): 发现阶段输出字典。
    evidence_items (list[dict[str, Any]]): 验证阶段生成的证据列表。
    run_dir (Path): 本次运行目录。
    chain_tx_metrics (list[dict[str, Any]]): 链上指标列表（就地追加）。

    返回值：
    dict[str, Any]: 治理阶段指标与证据 CID 信息。
    """
    mode = str(config.get("governance_mode", "both")).lower()
    gov_cfg = dict(config.get("governance", {}))
    metrics: list[dict[str, Any]] = []
    case_assertions: list[dict[str, Any]] = []

    holders = list(discovery_result.get("holders", []))
    if not holders:
        return {"governance_metrics": metrics, "evidence_cid": "", "case_assertions": case_assertions}
    target_agent = str(holders[0]["admin_address"])

    if not evidence_items:
        evidence_items = [
            {
                "source": "fallback",
                "message": "验证阶段未生成失败证据，使用占位证据触发治理留痕",
                "timestamp": time.time(),
            }
        ]
    evidence_payload = {
        "created_at": time.time(),
        "target_agent": target_agent,
        "run_dir": str(run_dir),
        "evidence_items": evidence_items,
    }
    evidence_cid, cid_upload_seconds, cid_upload_bytes = cache_evidence_to_local_ipfs(evidence_payload)
    loaded_payload, cid_download_seconds, cid_download_bytes = load_local_evidence_cid(evidence_cid)
    cid_roundtrip_ok = loaded_payload == evidence_payload
    metrics.append(
        {
            "mode": "local",
            "action": "cid_io",
            "status": "passed" if cid_roundtrip_ok else "failed",
            "cid_scope": "governance_evidence",
            "cid": evidence_cid,
            "io_direction": "upload",
            "io_backend": "local_cache",
            "io_seconds": cid_upload_seconds,
            "payload_bytes": cid_upload_bytes,
        }
    )
    metrics.append(
        {
            "mode": "local",
            "action": "cid_io",
            "status": "passed" if cid_roundtrip_ok else "failed",
            "cid_scope": "governance_evidence",
            "cid": evidence_cid,
            "io_direction": "download",
            "io_backend": "local_cache",
            "io_seconds": cid_download_seconds,
            "payload_bytes": cid_download_bytes,
        }
    )
    case_assertions.append(
        build_case_assertion(
            case_id="governance_evidence_cid_roundtrip",
            capability_id="governance.evidence_cid_roundtrip",
            expected="evidence CID roundtrip should equal source payload",
            actual=(
                f"ok={cid_roundtrip_ok} upload_s={cid_upload_seconds:.6f} "
                f"download_s={cid_download_seconds:.6f} bytes={cid_upload_bytes}"
            ),
            passed=cid_roundtrip_ok,
            error="" if cid_roundtrip_ok else "evidence CID payload mismatch",
        )
    )
    if not cid_roundtrip_ok:
        raise AssertionError("治理证据 CID roundtrip 校验失败")
    emit_progress(f"证据已固化到本地 CID: {evidence_cid}")

    if mode in {"sepolia", "both"}:
        api_url = str(root_key_config.get("api_url", "")).strip()
        if not api_url:
            raise ValueError("治理阶段缺少 root api_url")
        w3 = Web3(Web3.HTTPProvider(api_url))
        if not w3.is_connected():
            raise RuntimeError(f"治理阶段 RPC 连接失败: {api_url}")
        master = root_key_config.get("accounts", {}).get("master")
        if not isinstance(master, dict):
            raise ValueError("治理阶段缺少 master 账户配置")

        gov_metric, tx_metric = submit_sepolia_report(
            w3=w3,
            registry_address=str(
                gov_cfg.get("registry_address", "0x28249C2F09eF3196c1B42a0110dDD02D3B2b59B7")
            ),
            reporter_address=str(master["address"]),
            reporter_private_key=str(master["private_key"]),
            target_agent_address=target_agent,
            evidence_cid=evidence_cid,
        )
        metrics.append(gov_metric)
        chain_tx_metrics.append(tx_metric)
        sepolia_passed = str(gov_metric.get("status")) == "passed"
        case_assertions.append(
            build_case_assertion(
                case_id="governance_sepolia_report_misbehavior",
                capability_id="governance.report_misbehavior_onchain",
                expected="reportMisbehavior 上链成功",
                actual=f"status={gov_metric.get('status')} tx_hash={gov_metric.get('tx_hash')}",
                passed=sepolia_passed,
                error="" if sepolia_passed else "reportMisbehavior 交易失败",
            )
        )
        if not sepolia_passed:
            raise AssertionError("治理断言失败: Sepolia reportMisbehavior 未通过")
        emit_progress(f"Sepolia 举报已提交: {gov_metric.get('tx_hash')}")

    if mode in {"local", "both"}:
        script_path = str(gov_cfg.get("local_script", "fullflow_tests/contracts/local_governance.js"))
        emit_progress(f"执行本地治理脚本: {script_path}")
        metric = run_local_governance_script(script_path=script_path, cwd=Path(".").resolve())
        payload = metric.get("payload", {})
        local_ok = False
        local_error = ""
        if str(metric.get("status")) == "passed" and isinstance(payload, dict):
            local_ok, local_error = validate_local_governance_payload(payload)
        if not local_ok and not local_error and str(metric.get("status")) != "passed":
            local_error = f"脚本执行失败 return_code={metric.get('return_code')}"
        if not local_ok:
            metric["status"] = "failed"
            metric["validation_error"] = local_error

        metrics.append(metric)
        case_assertions.append(
            build_case_assertion(
                case_id="governance_local_full_action_set",
                capability_id="governance.local_report_slash_restore_freeze_unfreeze_appeal",
                expected="本地治理完成 report/slash/restore/freeze/unfreeze/appeal",
                actual=f"status={metric.get('status')} error={metric.get('validation_error', '')}",
                passed=local_ok,
                error=local_error if not local_ok else "",
            )
        )
        if not local_ok:
            raise AssertionError(f"治理断言失败: 本地治理结果不完整 -> {local_error}")
        emit_progress(f"本地治理状态: {metric.get('status')}")

    if mode == "off":
        metrics.append(
            {
                "mode": "off",
                "action": "skip",
                "status": "passed",
                "message": "governance_mode=off，跳过治理执行",
            }
        )
        case_assertions.append(
            build_case_assertion(
                case_id="governance_skip",
                capability_id="governance.skip_mode",
                expected="治理被显式关闭",
                actual="governance_mode=off",
                passed=True,
            )
        )

    return {
        "evidence_cid": evidence_cid,
        "governance_metrics": metrics,
        "case_assertions": case_assertions,
    }
