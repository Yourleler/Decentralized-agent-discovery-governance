from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
import uuid

import requests

from agents.verifier.runtime import VerifierRuntime
from infrastructure.validator import DIDValidator
from infrastructure.wallet import IdentityWallet


def emit_progress(message: str) -> None:
    """
    功能：
    输出 verification 阶段进度日志。

    参数：
    message (str): 进度消息文本。

    返回值：
    None: 仅打印日志，不返回数据。
    """
    ts = time.strftime("%H:%M:%S")
    print(f"[fullflow][{ts}][VERIFICATION] {message}", flush=True)


def build_case_assertion(
    case_id: str,
    capability_id: str,
    expected: str,
    actual: str,
    passed: bool,
    phase: str = "verification",
    error: str = "",
) -> dict[str, Any]:
    """
    功能：
    构造统一用例断言结构，供 case_assertions.csv 汇总。

    参数：
    case_id (str): 用例唯一 ID。
    capability_id (str): 能力标识。
    expected (str): 期望描述。
    actual (str): 实际描述。
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


def spawn_process(
    command: list[str],
    cwd: Path,
    log_path: Path | None = None,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.Popen:
    """
    功能：
    启动一个子进程并返回进程对象，可选将输出写入日志文件。

    参数：
    command (list[str]): 子进程命令列表。
    cwd (Path): 子进程工作目录。
    log_path (Path | None): 可选日志文件路径，提供时写入 stdout/stderr。

    返回值：
    subprocess.Popen: 启动后的进程对象。
    """
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if env_overrides:
        env.update({str(k): str(v) for k, v in env_overrides.items()})
    stdout_target: Any = subprocess.DEVNULL
    stderr_target: Any = subprocess.DEVNULL
    log_handle = None
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("a", encoding="utf-8")
        stdout_target = log_handle
        stderr_target = subprocess.STDOUT

    proc = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=stdout_target,
        stderr=stderr_target,
        env=env,
    )
    if log_handle is not None:
        setattr(proc, "_fullflow_log_handle", log_handle)
    return proc


def read_log_tail(log_path: Path, max_lines: int = 40) -> str:
    """
    功能：
    读取日志文件尾部内容，便于在进程启动失败时快速定位原因。

    参数：
    log_path (Path): 日志文件路径。
    max_lines (int): 读取末尾行数上限。

    返回值：
    str: 日志末尾文本；文件不存在或读取失败时返回空字符串。
    """
    if not log_path.exists():
        return ""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()
    if not lines:
        return ""
    return "\n".join(lines[-max_lines:])


def wait_port_open(
    host: str,
    port: int,
    timeout_seconds: float,
    process: subprocess.Popen | None = None,
    process_name: str = "service",
    log_path: Path | None = None,
    heartbeat_seconds: float = 5.0,
) -> None:
    """
    功能：
    在超时范围内轮询 TCP 端口，并在子进程提前退出时快速失败并输出日志线索。

    参数：
    host (str): 主机地址。
    port (int): 端口号。
    timeout_seconds (float): 超时时间（秒）。
    process (subprocess.Popen | None): 可选子进程对象，用于检测是否提前退出。
    process_name (str): 进程名称，用于错误提示。
    log_path (Path | None): 日志文件路径，用于失败时附带日志尾部。
    heartbeat_seconds (float): 等待期间进度心跳间隔（秒）。

    返回值：
    None: 端口可连接时正常返回；失败时抛出异常。
    """
    deadline = time.time() + timeout_seconds
    next_heartbeat = time.time() + max(heartbeat_seconds, 1.0)
    while time.time() < deadline:
        if process is not None and process.poll() is not None:
            exit_code = process.returncode
            tail = read_log_tail(log_path) if log_path else ""
            detail = f"{process_name} 启动失败，进程已退出，exit_code={exit_code}"
            if tail:
                detail += f"\n[{process_name} 日志尾部]\n{tail}"
            raise RuntimeError(detail)

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            if sock.connect_ex((host, port)) == 0:
                return

        now = time.time()
        if now >= next_heartbeat:
            elapsed = timeout_seconds - max(0.0, deadline - now)
            emit_progress(
                f"等待 {process_name} 端口 {port} 就绪中... 已等待 {elapsed:.1f}s / {timeout_seconds:.1f}s"
            )
            next_heartbeat = now + max(heartbeat_seconds, 1.0)
        time.sleep(0.5)

    tail = read_log_tail(log_path) if log_path else ""
    detail = f"{process_name} 启动超时: 端口 {port} 未就绪（{timeout_seconds:.1f}s）"
    if tail:
        detail += f"\n[{process_name} 日志尾部]\n{tail}"
    raise TimeoutError(detail)


def terminate_processes(processes: list[subprocess.Popen]) -> None:
    """
    功能：
    尝试优雅终止进程列表中的所有子进程。

    参数：
    processes (list[subprocess.Popen]): 待终止进程对象列表。

    返回值：
    None: 函数执行后进程被终止或强制杀死。
    """
    for proc in processes:
        if proc.poll() is None:
            proc.terminate()
    for proc in processes:
        if proc.poll() is None:
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
    for proc in processes:
        handle = getattr(proc, "_fullflow_log_handle", None)
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass


def compute_round_tps(success_rows: list[dict[str, Any]]) -> float:
    """
    功能：
    按“成功审计数量 / 最慢总时长”计算单轮吞吐率。

    参数：
    success_rows (list[dict[str, Any]]): 当前轮次成功的审计结果行。

    返回值：
    float: 当前轮次 TPS 数值。
    """
    if not success_rows:
        return 0.0
    max_duration = max(float(item.get("Total_Duration") or 0.0) for item in success_rows)
    if max_duration <= 0:
        return 0.0
    return float(len(success_rows)) / max_duration


def reset_holder_memory(holder_port: int, verifier_did: str, timeout_seconds: float = 20.0) -> bool:
    """
    功能：
    调用 Holder 的 `reset_memory` 接口，清理指定 verifier_did 对应的历史上下文。

    参数：
    holder_port (int): Holder 服务端口。
    verifier_did (str): 需要清理的 verifier DID。
    timeout_seconds (float): 请求超时时间（秒）。

    返回值：
    bool: 清理成功返回 True；失败返回 False。
    """
    try:
        response = requests.post(
            f"http://localhost:{holder_port}/reset_memory",
            json={"verifier_did": verifier_did},
            timeout=timeout_seconds,
        )
        return response.status_code == 200
    except requests.RequestException:
        return False


def run_single_pair_audit(
    pair_cfg: dict[str, Any],
    round_index: int,
    key_config: dict[str, Any],
    runtime_base_dir: Path,
    runtime: VerifierRuntime | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    功能：
    对单个 Verifier->Holder 对执行一次完整审计并返回指标与证据。

    参数：
    pair_cfg (dict[str, Any]): 对组配置，包含 name/verifier_role/holder_port。
    round_index (int): 当前轮次编号（从 1 开始）。
    key_config (dict[str, Any]): agents_4_key 配置字典。
    runtime_base_dir (Path): 运行时数据目录根路径。
    runtime (VerifierRuntime | None): 可复用的 Runtime 实例。

    返回值：
    tuple[dict[str, Any], list[dict[str, Any]]]:
    (单轮指标行, 失败证据列表)。
    """
    pair_name = str(pair_cfg["name"])
    verifier_role = str(pair_cfg["verifier_role"])
    holder_port = int(pair_cfg["holder_port"])
    holder_url = f"http://localhost:{holder_port}"
    runtime_dir = runtime_base_dir / f"{pair_name}"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    evidence_items: list[dict[str, Any]] = []
    row: dict[str, Any] = {
        "scenario": "positive",
        "case_id": f"verification_positive_{pair_name}_round_{round_index}",
        "capability_id": "verification.auth_probe_context_positive",
        "round": round_index,
        "pair_name": pair_name,
        "verifier_role": verifier_role,
        "holder_port": holder_port,
        "auth_success": False,
        "probe_success": False,
        "context_success": False,
        "SLA_Load_Ratio": 0.0,
        "status": "failed",
        "error": "",
    }

    started = time.time()
    runtime_obj = runtime
    if runtime_obj is None:
        runtime_obj = VerifierRuntime(
            role_name=verifier_role,
            config=key_config,
            instance_name=pair_name,
            data_dir=str(runtime_dir),
            target_holder_url=holder_url,
        )

    auth_ok, auth_msg, holder_did, auth_times = runtime_obj.execute_auth()
    row["auth_success"] = bool(auth_ok)
    row["auth_msg"] = auth_msg
    t1, t2, t3 = auth_times
    row["T1"] = t1 - started
    row["T2"] = t2 - t1
    row["T3"] = t3 - t2
    row["T4"] = row["T2"] + row["T3"]

    if not auth_ok:
        row["error"] = f"AUTH_FAIL: {auth_msg}"
        evidence_items.append(
            {
                "source": "positive_flow",
                "pair_name": pair_name,
                "round": round_index,
                "stage": "auth",
                "message": auth_msg,
                "holder_did": holder_did or "",
                "timestamp": time.time(),
            }
        )
        return row, evidence_items

    probe_ok, probe_msg, probe_times = runtime_obj.execute_probe(holder_did or "")
    row["probe_success"] = bool(probe_ok)
    row["probe_msg"] = probe_msg
    p1, p2, p3, sla_ratio = probe_times
    row["T5"] = p1 - t3
    row["T6"] = p2 - p1
    row["T7"] = p3 - p2
    row["T8"] = row["T6"] + row["T7"]
    row["SLA_Load_Ratio"] = float(sla_ratio)

    if not probe_ok:
        row["error"] = f"PROBE_FAIL: {probe_msg}"
        evidence_items.append(
            {
                "source": "positive_flow",
                "pair_name": pair_name,
                "round": round_index,
                "stage": "probe",
                "message": probe_msg,
                "holder_did": holder_did or "",
                "timestamp": time.time(),
            }
        )
        return row, evidence_items

    ctx_ok, ctx_msg, ctx_times = runtime_obj.execute_context_check(holder_did or "")
    row["context_success"] = bool(ctx_ok)
    row["context_msg"] = ctx_msg
    c1, c2, c3 = ctx_times
    row["T9"] = c1 - p3
    row["T10"] = c2 - c1
    row["T11"] = c3 - c2
    row["T12"] = row["T10"] + row["T11"]
    row["Total_Duration"] = row["T4"] + row["T8"] + row["T12"]

    if not ctx_ok:
        row["error"] = f"CONTEXT_FAIL: {ctx_msg}"
        evidence_items.append(
            {
                "source": "positive_flow",
                "pair_name": pair_name,
                "round": round_index,
                "stage": "context",
                "message": ctx_msg,
                "holder_did": holder_did or "",
                "timestamp": time.time(),
            }
        )
        return row, evidence_items

    row["status"] = "passed"
    return row, evidence_items


def run_fake_signature_negative(
    holder_port: int,
    verifier_role: str,
    key_config: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    功能：
    执行“伪造签名认证请求”负例，期望 Holder 返回 401。

    参数：
    holder_port (int): 目标 Holder 端口。
    verifier_role (str): 用于生成 verifier_did 的角色名。
    key_config (dict[str, Any]): agents_4_key 配置字典。

    返回值：
    tuple[dict[str, Any], list[dict[str, Any]]]:
    (负例指标行, 证据列表)。
    """
    wallet = IdentityWallet(verifier_role, override_config=key_config)
    payload = {
        "nonce": str(uuid.uuid4()),
        "verifier_did": wallet.did,
        "type": "AuthRequest",
        "timestamp": time.time(),
        "verifier_signature": "0xdeadbeef",
    }
    resp = requests.post(f"http://localhost:{holder_port}/auth", json=payload, timeout=30)
    passed = resp.status_code == 401

    row = {
        "scenario": "negative",
        "case_id": "verification_negative_fake_signature_auth",
        "capability_id": "verification.auth_signature_reject",
        "negative_case": "fake_signature_auth",
        "holder_port": holder_port,
        "expected_status_code": 401,
        "actual_status_code": resp.status_code,
        "status": "passed" if passed else "failed",
        "error": "" if passed else f"期望401，实际{resp.status_code}",
    }
    evidence = [
        {
            "source": "negative_test",
            "case": "fake_signature_auth",
            "payload": payload,
            "response_status": resp.status_code,
            "response_body": resp.text[:500],
            "timestamp": time.time(),
        }
    ]
    return row, evidence


def run_context_mismatch_negative(
    holder_port: int,
    verifier_role: str,
    key_config: dict[str, Any],
    runtime_base_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    功能：
    执行“上下文失配”负例，先清空 Holder 记忆再发起 context check。

    参数：
    holder_port (int): 目标 Holder 端口。
    verifier_role (str): Verifier 角色名。
    key_config (dict[str, Any]): agents_4_key 配置字典。
    runtime_base_dir (Path): 运行时目录根路径。

    返回值：
    tuple[dict[str, Any], list[dict[str, Any]]]:
    (负例指标行, 证据列表)。
    """
    holder_url = f"http://localhost:{holder_port}"
    runtime_dir = runtime_base_dir / "neg_ctx"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    negative_verifier_did = IdentityWallet(verifier_role, override_config=key_config).did
    reset_holder_memory(holder_port=holder_port, verifier_did=negative_verifier_did)
    runtime = VerifierRuntime(
        role_name=verifier_role,
        config=key_config,
        instance_name="negative-context",
        data_dir=str(runtime_dir),
        target_holder_url=holder_url,
    )
    auth_ok, auth_msg, holder_did, _ = runtime.execute_auth()
    if not auth_ok:
        row = {
            "scenario": "negative",
            "case_id": "verification_negative_context_mismatch",
            "capability_id": "verification.context_consistency_reject",
            "negative_case": "context_mismatch",
            "status": "failed",
            "error": f"负例前置 auth 失败: {auth_msg}",
        }
        evidence = [
            {
                "source": "negative_test",
                "case": "context_mismatch",
                "stage": "auth",
                "message": auth_msg,
                "timestamp": time.time(),
            }
        ]
        return row, evidence

    probe_ok, probe_msg, _ = runtime.execute_probe(holder_did or "")
    if not probe_ok:
        row = {
            "scenario": "negative",
            "case_id": "verification_negative_context_mismatch",
            "capability_id": "verification.context_consistency_reject",
            "negative_case": "context_mismatch",
            "status": "failed",
            "error": f"负例前置 probe 失败: {probe_msg}",
        }
        evidence = [
            {
                "source": "negative_test",
                "case": "context_mismatch",
                "stage": "probe",
                "message": probe_msg,
                "timestamp": time.time(),
            }
        ]
        return row, evidence

    reset_resp = requests.post(
        f"{holder_url}/reset_memory",
        json={"verifier_did": runtime.wallet.did},
        timeout=20,
    )
    ctx_ok, ctx_msg, _ = runtime.execute_context_check(holder_did or "")
    passed = (reset_resp.status_code == 200) and (not ctx_ok)
    row = {
        "scenario": "negative",
        "case_id": "verification_negative_context_mismatch",
        "capability_id": "verification.context_consistency_reject",
        "negative_case": "context_mismatch",
        "reset_status_code": reset_resp.status_code,
        "context_result": bool(ctx_ok),
        "context_message": ctx_msg,
        "status": "passed" if passed else "failed",
        "error": "" if passed else "重置后仍未触发 Context Mismatch",
    }
    evidence = [
        {
            "source": "negative_test",
            "case": "context_mismatch",
            "reset_status_code": reset_resp.status_code,
            "context_result": bool(ctx_ok),
            "context_message": ctx_msg,
            "timestamp": time.time(),
        }
    ]
    return row, evidence


def request_valid_auth_vp(
    holder_port: int,
    verifier_role: str,
    key_config: dict[str, Any],
) -> tuple[bool, str, dict[str, Any] | None, str]:
    """
    功能：
    发起一次合法 auth 请求并返回 VP，供篡改负例复用。

    参数：
    holder_port (int): 目标 Holder 端口。
    verifier_role (str): Verifier 角色名。
    key_config (dict[str, Any]): agents_4_key 配置。

    返回值：
    tuple[bool, str, dict[str, Any] | None, str]:
    (是否成功, 消息, VP对象, 原始nonce)。
    """
    wallet = IdentityWallet(verifier_role, override_config=key_config)
    nonce = str(uuid.uuid4())
    payload = {
        "nonce": nonce,
        "verifier_did": wallet.did,
        "type": "AuthRequest",
        "timestamp": time.time(),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["verifier_signature"] = wallet.sign_message(serialized)
    try:
        resp = requests.post(f"http://localhost:{holder_port}/auth", json=payload, timeout=30)
    except requests.RequestException as exc:
        return False, f"请求异常: {exc}", None, nonce
    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}", None, nonce
    try:
        vp = resp.json()
    except ValueError:
        return False, "返回体不是 JSON", None, nonce
    if not isinstance(vp, dict):
        return False, "返回 VP 结构非法", None, nonce
    return True, "ok", vp, nonce


def run_tampered_vp_challenge_negative(
    holder_port: int,
    verifier_role: str,
    key_config: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    功能：
    执行“篡改 VP challenge”负例，期望 verify_vp 失败。

    参数：
    holder_port (int): 目标 Holder 端口。
    verifier_role (str): Verifier 角色名。
    key_config (dict[str, Any]): agents_4_key 配置。

    返回值：
    tuple[dict[str, Any], list[dict[str, Any]]]:
    (负例指标行, 证据列表)。
    """
    ok, msg, vp, nonce = request_valid_auth_vp(holder_port=holder_port, verifier_role=verifier_role, key_config=key_config)
    if not ok or vp is None:
        row = {
            "scenario": "negative",
            "case_id": "verification_negative_tampered_vp_challenge",
            "capability_id": "verification.vp_challenge_integrity",
            "negative_case": "tampered_vp_challenge",
            "status": "failed",
            "error": f"负例前置 auth 失败: {msg}",
        }
        evidence = [{"source": "negative_test", "case": "tampered_vp_challenge", "message": msg, "timestamp": time.time()}]
        return row, evidence

    tampered_vp = copy.deepcopy(vp)
    proof = tampered_vp.get("proof") if isinstance(tampered_vp.get("proof"), dict) else {}
    proof["challenge"] = str(uuid.uuid4())
    tampered_vp["proof"] = proof

    validator = DIDValidator()
    valid, reason = validator.verify_vp(tampered_vp, expected_nonce=nonce)
    passed = not valid
    row = {
        "scenario": "negative",
        "case_id": "verification_negative_tampered_vp_challenge",
        "capability_id": "verification.vp_challenge_integrity",
        "negative_case": "tampered_vp_challenge",
        "expected_result": "verify_vp=False",
        "actual_result": f"verify_vp={valid}",
        "validator_reason": reason,
        "status": "passed" if passed else "failed",
        "error": "" if passed else "篡改 challenge 后仍通过 verify_vp",
    }
    evidence = [
        {
            "source": "negative_test",
            "case": "tampered_vp_challenge",
            "original_nonce": nonce,
            "tampered_challenge": proof.get("challenge"),
            "validator_reason": reason,
            "timestamp": time.time(),
        }
    ]
    return row, evidence


def run_tampered_vp_signature_negative(
    holder_port: int,
    verifier_role: str,
    key_config: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    功能：
    执行“篡改 VP 签名”负例，期望 verify_vp 失败。

    参数：
    holder_port (int): 目标 Holder 端口。
    verifier_role (str): Verifier 角色名。
    key_config (dict[str, Any]): agents_4_key 配置。

    返回值：
    tuple[dict[str, Any], list[dict[str, Any]]]:
    (负例指标行, 证据列表)。
    """
    ok, msg, vp, nonce = request_valid_auth_vp(holder_port=holder_port, verifier_role=verifier_role, key_config=key_config)
    if not ok or vp is None:
        row = {
            "scenario": "negative",
            "case_id": "verification_negative_tampered_vp_signature",
            "capability_id": "verification.vp_signature_integrity",
            "negative_case": "tampered_vp_signature",
            "status": "failed",
            "error": f"负例前置 auth 失败: {msg}",
        }
        evidence = [{"source": "negative_test", "case": "tampered_vp_signature", "message": msg, "timestamp": time.time()}]
        return row, evidence

    tampered_vp = copy.deepcopy(vp)
    proof = tampered_vp.get("proof") if isinstance(tampered_vp.get("proof"), dict) else {}
    origin_sig = str(proof.get("jws", ""))
    if origin_sig.startswith("0x") and len(origin_sig) > 4:
        tail = "0" if origin_sig[-1] != "0" else "1"
        tampered_sig = origin_sig[:-1] + tail
    elif len(origin_sig) > 1:
        tampered_sig = origin_sig[:-1] + ("a" if origin_sig[-1] != "a" else "b")
    else:
        tampered_sig = origin_sig + "0"
    proof["jws"] = tampered_sig
    tampered_vp["proof"] = proof

    validator = DIDValidator()
    valid, reason = validator.verify_vp(tampered_vp, expected_nonce=nonce)
    passed = not valid
    row = {
        "scenario": "negative",
        "case_id": "verification_negative_tampered_vp_signature",
        "capability_id": "verification.vp_signature_integrity",
        "negative_case": "tampered_vp_signature",
        "expected_result": "verify_vp=False",
        "actual_result": f"verify_vp={valid}",
        "validator_reason": reason,
        "status": "passed" if passed else "failed",
        "error": "" if passed else "篡改签名后仍通过 verify_vp",
    }
    evidence = [
        {
            "source": "negative_test",
            "case": "tampered_vp_signature",
            "original_signature_prefix": origin_sig[:20],
            "tampered_signature_prefix": tampered_sig[:20],
            "validator_reason": reason,
            "timestamp": time.time(),
        }
    ]
    return row, evidence


def run_verification_flow(
    config: dict[str, Any],
    key_config: dict[str, Any],
    key_path: str,
    run_dir: Path,
) -> dict[str, Any]:
    """
    功能：
    执行验证闭环：启动 Issuer/Holders，进行 2v2 多轮审计与负例测试。

    参数：
    config (dict[str, Any]): 全流程配置字典。
    key_config (dict[str, Any]): agents_4_key 配置字典。
    key_path (str): agents_4_key.json 文件路径。
    run_dir (Path): 本次运行目录。

    返回值：
    dict[str, Any]: 验证阶段指标、证据与轮次摘要。
    """
    verify_cfg = dict(config.get("verification", {}))
    rounds = int(config.get("rounds", 3))
    holder_roles = list(verify_cfg.get("holder_roles", ["agent_a_op", "agent_b_op"]))
    holder_start_port = int(verify_cfg.get("holder_start_port", 5000))
    issuer_port = int(verify_cfg.get("issuer_port", 8000))
    pairs = list(
        verify_cfg.get(
            "pairs",
            [
                {"name": "pair_c_to_a", "verifier_role": "agent_c_op", "holder_port": 5000},
                {"name": "pair_d_to_b", "verifier_role": "agent_d_op", "holder_port": 5001},
            ],
        )
    )
    negative_verifier_role = str(verify_cfg.get("negative_verifier_role", "agent_c_op"))

    phase_metrics: list[dict[str, Any]] = []
    evidence_items: list[dict[str, Any]] = []
    round_summaries: list[dict[str, Any]] = []
    case_assertions: list[dict[str, Any]] = []

    processes: list[subprocess.Popen] = []
    runtime_base_dir = run_dir / "verification_runtime"
    runtime_base_dir.mkdir(parents=True, exist_ok=True)
    process_log_dir = run_dir / "verification_process_logs"
    process_log_dir.mkdir(parents=True, exist_ok=True)
    runtime_pool: dict[str, VerifierRuntime] = {}
    shared_state_db_path = run_dir / "discovery_sidecar_state.db"

    project_root = Path(".").resolve()

    try:
        emit_progress("启动 Issuer 服务")
        issuer_log = process_log_dir / "issuer.log"
        issuer_proc = spawn_process(
            [sys.executable, "_ops_services/issuer_server.py"],
            cwd=project_root,
            log_path=issuer_log,
        )
        processes.append(issuer_proc)
        emit_progress(f"Issuer 日志: {issuer_log}")
        wait_port_open(
            "127.0.0.1",
            issuer_port,
            timeout_seconds=60,
            process=issuer_proc,
            process_name="Issuer",
            log_path=issuer_log,
        )
        emit_progress(f"Issuer 已就绪: {issuer_port}")

        holder_wait_items: list[tuple[int, str, subprocess.Popen, Path]] = []
        for idx, role in enumerate(holder_roles):
            port = holder_start_port + idx
            holder_log = process_log_dir / f"holder_{role}_{port}.log"
            emit_progress(f"启动 Holder {role} 端口 {port}")
            proc = spawn_process(
                [
                    sys.executable,
                    "agents/holder/runtime.py",
                    str(port),
                    role,
                    str(Path(key_path).resolve()),
                ],
                cwd=project_root,
                log_path=holder_log,
                env_overrides={"AGENT_RUNTIME_DB_PATH": str(shared_state_db_path)},
            )
            processes.append(proc)
            holder_wait_items.append((port, role, proc, holder_log))
            emit_progress(f"Holder {role} 日志: {holder_log}")

        for port, role, proc, holder_log in holder_wait_items:
            wait_port_open(
                "127.0.0.1",
                port,
                timeout_seconds=90,
                process=proc,
                process_name=f"Holder({role})",
                log_path=holder_log,
            )
            emit_progress(f"Holder 已就绪: {port}")

        for pair in pairs:
            pair_name = str(pair["name"])
            verifier_role = str(pair["verifier_role"])
            holder_port = int(pair["holder_port"])
            holder_url = f"http://localhost:{holder_port}"
            pair_runtime_dir = runtime_base_dir / pair_name
            pair_runtime_dir.mkdir(parents=True, exist_ok=True)
            emit_progress(f"初始化 Runtime: {pair_name} ({verifier_role} -> {holder_url})")
            runtime_pool[pair_name] = VerifierRuntime(
                role_name=verifier_role,
                config=key_config,
                instance_name=pair_name,
                data_dir=str(pair_runtime_dir),
                target_holder_url=holder_url,
                state_db_path=str(shared_state_db_path),
            )

        emit_progress("预清理 Holder 记忆，避免历史数据影响 context 校验")
        for pair in pairs:
            verifier_role = str(pair["verifier_role"])
            holder_port = int(pair["holder_port"])
            verifier_did = IdentityWallet(verifier_role, override_config=key_config).did
            reset_ok = reset_holder_memory(holder_port=holder_port, verifier_did=verifier_did)
            emit_progress(
                f"Holder({holder_port}) 清理 {verifier_role} 记忆: {'ok' if reset_ok else 'failed'}"
            )

        for round_index in range(1, rounds + 1):
            emit_progress(f"开始第 {round_index}/{rounds} 轮审计")
            round_rows: list[dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=max(1, len(pairs))) as pool:
                future_map = {
                    pool.submit(
                        run_single_pair_audit,
                        pair_cfg=pair,
                        round_index=round_index,
                        key_config=key_config,
                        runtime_base_dir=runtime_base_dir,
                        runtime=runtime_pool.get(str(pair["name"])),
                    ): pair
                    for pair in pairs
                }
                for future in as_completed(future_map):
                    row, evidence = future.result()
                    round_rows.append(row)
                    evidence_items.extend(evidence)
                    phase_metrics.append(row)
                    case_assertions.append(
                        build_case_assertion(
                            case_id=str(row.get("case_id", "")),
                            capability_id=str(row.get("capability_id", "verification.auth_probe_context_positive")),
                            expected="auth/probe/context 全通过",
                            actual=f"status={row.get('status')} auth={row.get('auth_success')} probe={row.get('probe_success')} context={row.get('context_success')}",
                            passed=str(row.get("status")) == "passed",
                            error=str(row.get("error", "")),
                        )
                    )
                    emit_progress(
                        f"轮次{round_index} {row.get('pair_name')} -> {row.get('status')} "
                        f"(auth={row.get('auth_success')} probe={row.get('probe_success')} context={row.get('context_success')})"
                    )

            success_rows = [item for item in round_rows if item.get("status") == "passed"]
            failed_rows = [item for item in round_rows if item.get("status") != "passed"]
            if failed_rows:
                first = failed_rows[0]
                raise AssertionError(
                    f"正向用例失败: pair={first.get('pair_name')} round={first.get('round')} error={first.get('error')}"
                )
            round_tps = compute_round_tps(success_rows)
            round_summary = {
                "scenario": "round_summary",
                "round": round_index,
                "pair_count": len(round_rows),
                "success_count": len(success_rows),
                "round_tps": round_tps,
            }
            round_summaries.append(round_summary)
            phase_metrics.append(round_summary)
            emit_progress(
                f"第 {round_index} 轮完成: success={len(success_rows)}/{len(round_rows)} tps={round_tps:.4f}"
            )

        emit_progress("执行负例: fake_signature_auth")
        neg_row_a, neg_evidence_a = run_fake_signature_negative(
            holder_port=holder_start_port,
            verifier_role=negative_verifier_role,
            key_config=key_config,
        )
        phase_metrics.append(neg_row_a)
        evidence_items.extend(neg_evidence_a)
        case_assertions.append(
            build_case_assertion(
                case_id=str(neg_row_a.get("case_id", "verification_negative_fake_signature_auth")),
                capability_id=str(neg_row_a.get("capability_id", "verification.auth_signature_reject")),
                expected="伪造签名请求返回401并失败",
                actual=f"status={neg_row_a.get('status')} http={neg_row_a.get('actual_status_code', '')}",
                passed=str(neg_row_a.get("status")) == "passed",
                error=str(neg_row_a.get("error", "")),
            )
        )
        if str(neg_row_a.get("status")) != "passed":
            raise AssertionError(f"负例失败: fake_signature_auth -> {neg_row_a.get('error')}")

        emit_progress("执行负例: context_mismatch")
        neg_row_b, neg_evidence_b = run_context_mismatch_negative(
            holder_port=holder_start_port,
            verifier_role=negative_verifier_role,
            key_config=key_config,
            runtime_base_dir=runtime_base_dir,
        )
        phase_metrics.append(neg_row_b)
        evidence_items.extend(neg_evidence_b)
        case_assertions.append(
            build_case_assertion(
                case_id=str(neg_row_b.get("case_id", "verification_negative_context_mismatch")),
                capability_id=str(neg_row_b.get("capability_id", "verification.context_consistency_reject")),
                expected="重置后 context 检查失败",
                actual=f"status={neg_row_b.get('status')} context_result={neg_row_b.get('context_result')}",
                passed=str(neg_row_b.get("status")) == "passed",
                error=str(neg_row_b.get("error", "")),
            )
        )
        if str(neg_row_b.get("status")) != "passed":
            raise AssertionError(f"负例失败: context_mismatch -> {neg_row_b.get('error')}")

        emit_progress("执行负例: tampered_vp_challenge")
        neg_row_c, neg_evidence_c = run_tampered_vp_challenge_negative(
            holder_port=holder_start_port,
            verifier_role=negative_verifier_role,
            key_config=key_config,
        )
        phase_metrics.append(neg_row_c)
        evidence_items.extend(neg_evidence_c)
        case_assertions.append(
            build_case_assertion(
                case_id=str(neg_row_c.get("case_id", "verification_negative_tampered_vp_challenge")),
                capability_id=str(neg_row_c.get("capability_id", "verification.vp_challenge_integrity")),
                expected="篡改 challenge 后 verify_vp 失败",
                actual=f"status={neg_row_c.get('status')} result={neg_row_c.get('actual_result', '')}",
                passed=str(neg_row_c.get("status")) == "passed",
                error=str(neg_row_c.get("error", "")),
            )
        )
        if str(neg_row_c.get("status")) != "passed":
            raise AssertionError(f"负例失败: tampered_vp_challenge -> {neg_row_c.get('error')}")

        emit_progress("执行负例: tampered_vp_signature")
        neg_row_d, neg_evidence_d = run_tampered_vp_signature_negative(
            holder_port=holder_start_port,
            verifier_role=negative_verifier_role,
            key_config=key_config,
        )
        phase_metrics.append(neg_row_d)
        evidence_items.extend(neg_evidence_d)
        case_assertions.append(
            build_case_assertion(
                case_id=str(neg_row_d.get("case_id", "verification_negative_tampered_vp_signature")),
                capability_id=str(neg_row_d.get("capability_id", "verification.vp_signature_integrity")),
                expected="篡改签名后 verify_vp 失败",
                actual=f"status={neg_row_d.get('status')} result={neg_row_d.get('actual_result', '')}",
                passed=str(neg_row_d.get("status")) == "passed",
                error=str(neg_row_d.get("error", "")),
            )
        )
        if str(neg_row_d.get("status")) != "passed":
            raise AssertionError(f"负例失败: tampered_vp_signature -> {neg_row_d.get('error')}")
    finally:
        emit_progress("停止 Issuer/Holder 进程")
        terminate_processes(processes)

    return {
        "phase_metrics": phase_metrics,
        "evidence_items": evidence_items,
        "round_summaries": round_summaries,
        "case_assertions": case_assertions,
    }
