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
from web3 import Web3

from agents.verifier.runtime import VerifierRuntime
from infrastructure.validator import DIDValidator
from infrastructure.wallet import IdentityWallet
from interop.request_policy import (
    build_authorization_details,
    build_request_signature_payload,
    with_request_envelope,
)


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


def compute_percentile(values: list[float], quantile: float) -> float:
    """
    功能：
    计算浮点数组的分位值（线性插值）。
    参数：
    values (list[float]): 输入样本。
    quantile (float): 分位点，范围 [0, 1]。
    返回值：
    float: 分位值；样本为空时返回 0.0。
    """
    if not values:
        return 0.0
    ordered = sorted(float(item) for item in values)
    if quantile <= 0:
        return float(ordered[0])
    if quantile >= 1:
        return float(ordered[-1])
    position = (len(ordered) - 1) * quantile
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    if low == high:
        return float(ordered[low])
    weight = position - low
    return float(ordered[low] * (1.0 - weight) + ordered[high] * weight)


def to_level_key(level_name: str, fallback: str = "l1") -> str:
    """
    功能：
    将负载档位名标准化为仅包含字母数字与下划线的 key。

    参数：
    level_name (str): 档位名，如 S/M/L 或 burst-1。
    fallback (str): 为空时的兜底 key。

    返回值：
    str: 标准化 key。
    """
    raw = str(level_name or "").strip().lower()
    if not raw:
        return fallback
    chars: list[str] = []
    for ch in raw:
        chars.append(ch if ch.isalnum() else "_")
    key = "".join(chars).strip("_")
    while "__" in key:
        key = key.replace("__", "_")
    return key or fallback


def run_concurrency_stress_positive(
    pairs: list[dict[str, Any]],
    key_config: dict[str, Any],
    runtime_base_dir: Path,
    tasks_per_pair: int,
    max_workers: int,
    min_pass_rate: float,
    load_level: str = "L1",
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """
    功能：
    执行正向并发压测（Auth + Probe），输出任务级与汇总级指标。
    参数：
    pairs (list[dict[str, Any]]): verifier->holder 对组配置。
    key_config (dict[str, Any]): agents_4_key 配置。
    runtime_base_dir (Path): 验证阶段运行目录。
    tasks_per_pair (int): 每个 pair 的并发任务数。
    max_workers (int): 并发线程数上限。
    min_pass_rate (float): 判定通过的最低通过率阈值。
    返回值：
    tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    (汇总行, 任务行列表, 证据列表)。
    """
    level_name = str(load_level or "L1")
    level_key = to_level_key(level_name)
    valid_pairs: list[dict[str, Any]] = []
    for pair in pairs:
        holder_port = int(pair.get("holder_port", 0) or 0)
        verifier_role = str(pair.get("verifier_role", "")).strip()
        if holder_port <= 0 or not verifier_role:
            continue
        valid_pairs.append(dict(pair))

    if not valid_pairs:
        summary_row = {
            "scenario": "concurrency_stress_summary",
            "case_id": f"verification_concurrency_stress_summary_{level_key}",
            "capability_id": "verification.concurrency_stress_positive",
            "load_level": level_name,
            "load_level_key": level_key,
            "status": "failed",
            "error": "未找到可用并发压测 pair 配置",
            "total_tasks": 0,
            "passed_tasks": 0,
            "failed_tasks": 0,
            "pass_rate": 0.0,
            "avg_duration_seconds": 0.0,
            "p50_duration_seconds": 0.0,
            "p95_duration_seconds": 0.0,
            "max_duration_seconds": 0.0,
            "wall_clock_seconds": 0.0,
            "throughput_tps": 0.0,
            "tasks_per_pair": max(1, int(tasks_per_pair)),
            "max_workers": max(1, int(max_workers)),
            "min_pass_rate": float(min_pass_rate),
        }
        evidence = [
            {
                "source": "concurrency_stress",
                "load_level": level_name,
                "message": "no_valid_pairs",
                "timestamp": time.time(),
            }
        ]
        return summary_row, [], evidence

    planned_tasks_per_pair = max(1, int(tasks_per_pair))
    total_tasks = planned_tasks_per_pair * len(valid_pairs)
    worker_count = max(1, int(max_workers))
    threshold = max(0.0, min(1.0, float(min_pass_rate)))

    started_at = time.time()
    task_rows: list[dict[str, Any]] = []
    task_evidence: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        future_map: dict[Any, dict[str, Any]] = {}
        for task_index in range(total_tasks):
            pair = dict(valid_pairs[task_index % len(valid_pairs)])
            pair_name = str(pair.get("name", f"pair_{task_index}"))
            future = pool.submit(
                run_single_pair_auth_probe_task,
                pair_cfg=pair,
                key_config=key_config,
                runtime_base_dir=runtime_base_dir / "concurrency_stress",
                task_index=task_index + 1,
                load_level=level_name,
            )
            future_map[future] = {
                "task_index": task_index + 1,
                "base_pair_name": pair_name,
            }

        for future in as_completed(future_map):
            meta = future_map[future]
            task_index = int(meta["task_index"])
            base_pair_name = str(meta["base_pair_name"])
            try:
                row, evidence_items = future.result()
                normalized_row = dict(row)
                normalized_row["base_pair_name"] = base_pair_name
                task_rows.append(normalized_row)
                task_evidence.extend(evidence_items)
            except Exception as exc:
                failed_row = {
                    "scenario": "concurrency_stress_task",
                    "case_id": f"verification_concurrency_stress_task_{level_key}_{task_index}",
                    "capability_id": "verification.concurrency_stress_positive",
                    "load_level": level_name,
                    "load_level_key": level_key,
                    "task_index": task_index,
                    "base_pair_name": base_pair_name,
                    "status": "failed",
                    "error": str(exc),
                    "Total_Duration": 0.0,
                    "auth_success": False,
                    "probe_success": False,
                    "context_success": False,
                }
                task_rows.append(failed_row)
                task_evidence.append(
                    {
                        "source": "concurrency_stress",
                        "load_level": level_name,
                        "task_index": task_index,
                        "base_pair_name": base_pair_name,
                        "message": str(exc),
                        "timestamp": time.time(),
                    }
                )

    wall_clock_seconds = max(0.0, time.time() - started_at)
    passed_tasks = sum(1 for row in task_rows if str(row.get("status")) == "passed")
    failed_tasks = max(0, len(task_rows) - passed_tasks)
    pass_rate = (float(passed_tasks) / float(max(1, len(task_rows)))) if task_rows else 0.0
    durations = [
        float(row.get("Total_Duration", 0.0))
        for row in task_rows
        if float(row.get("Total_Duration", 0.0)) > 0
    ]
    avg_duration = (sum(durations) / len(durations)) if durations else 0.0
    p50_duration = compute_percentile(durations, 0.50)
    p95_duration = compute_percentile(durations, 0.95)
    max_duration = max(durations) if durations else 0.0
    throughput_tps = (float(passed_tasks) / wall_clock_seconds) if wall_clock_seconds > 0 else 0.0
    passed = bool(task_rows) and pass_rate >= threshold

    failed_samples = [
        {
            "case_id": str(row.get("case_id", "")),
            "base_pair_name": str(row.get("base_pair_name", "")),
            "error": str(row.get("error", "")),
            "status": str(row.get("status", "")),
        }
        for row in task_rows
        if str(row.get("status")) != "passed"
    ][:5]

    summary_row = {
        "scenario": "concurrency_stress_summary",
        "case_id": f"verification_concurrency_stress_summary_{level_key}",
        "capability_id": "verification.concurrency_stress_positive",
        "load_level": level_name,
        "load_level_key": level_key,
        "status": "passed" if passed else "failed",
        "error": "" if passed else f"并发压测通过率不足: {pass_rate:.4f} < {threshold:.4f}",
        "total_tasks": len(task_rows),
        "passed_tasks": passed_tasks,
        "failed_tasks": failed_tasks,
        "pass_rate": round(pass_rate, 4),
        "avg_duration_seconds": round(avg_duration, 6),
        "p50_duration_seconds": round(p50_duration, 6),
        "p95_duration_seconds": round(p95_duration, 6),
        "max_duration_seconds": round(max_duration, 6),
        "wall_clock_seconds": round(wall_clock_seconds, 6),
        "throughput_tps": round(throughput_tps, 6),
        "tasks_per_pair": planned_tasks_per_pair,
        "max_workers": worker_count,
        "min_pass_rate": round(threshold, 4),
    }
    evidence = [
        {
            "source": "concurrency_stress",
            "load_level": level_name,
            "summary": summary_row,
            "failed_samples": failed_samples,
            "timestamp": time.time(),
        }
    ]
    evidence.extend(task_evidence[:20])
    return summary_row, task_rows, evidence


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


def run_single_pair_auth_probe_task(
    pair_cfg: dict[str, Any],
    key_config: dict[str, Any],
    runtime_base_dir: Path,
    task_index: int,
    load_level: str = "L1",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    功能：
    执行并发压测任务（Auth + Probe），用于衡量并发吞吐与延迟。
    参数：
    pair_cfg (dict[str, Any]): verifier->holder 对组配置。
    key_config (dict[str, Any]): agents_4_key 配置。
    runtime_base_dir (Path): 运行时目录根路径。
    task_index (int): 压测任务编号。
    返回值：
    tuple[dict[str, Any], list[dict[str, Any]]]:
    (任务指标行, 失败证据列表)。
    """
    pair_name = str(pair_cfg["name"])
    level_name = str(load_level or "L1")
    level_key = to_level_key(level_name)
    verifier_role = str(pair_cfg["verifier_role"])
    holder_port = int(pair_cfg["holder_port"])
    holder_url = f"http://localhost:{holder_port}"
    runtime_dir = runtime_base_dir / f"stress_task_{task_index}_{pair_name}"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    row: dict[str, Any] = {
        "scenario": "concurrency_stress_task",
        "case_id": f"verification_concurrency_stress_task_{level_key}_{task_index}",
        "capability_id": "verification.concurrency_stress_positive",
        "load_level": level_name,
        "load_level_key": level_key,
        "task_index": task_index,
        "pair_name": pair_name,
        "verifier_role": verifier_role,
        "holder_port": holder_port,
        "auth_success": False,
        "probe_success": False,
        "context_success": "skipped",
        "status": "failed",
        "error": "",
    }
    evidence_items: list[dict[str, Any]] = []

    started = time.time()
    runtime = VerifierRuntime(
        role_name=verifier_role,
        config=key_config,
        instance_name=f"{pair_name}_stress_{task_index}",
        data_dir=str(runtime_dir),
        target_holder_url=holder_url,
    )

    auth_ok, auth_msg, holder_did, auth_times = runtime.execute_auth()
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
                "source": "concurrency_stress",
                "load_level": level_name,
                "task_index": task_index,
                "pair_name": pair_name,
                "stage": "auth",
                "message": auth_msg,
                "holder_did": holder_did or "",
                "timestamp": time.time(),
            }
        )
        row["Total_Duration"] = row["T4"]
        return row, evidence_items

    probe_ok, probe_msg, probe_times = runtime.execute_probe(holder_did or "")
    row["probe_success"] = bool(probe_ok)
    row["probe_msg"] = probe_msg
    p1, p2, p3, sla_ratio = probe_times
    row["T5"] = p1 - t3
    row["T6"] = p2 - p1
    row["T7"] = p3 - p2
    row["T8"] = row["T6"] + row["T7"]
    row["SLA_Load_Ratio"] = float(sla_ratio)
    row["Total_Duration"] = row["T4"] + row["T8"]
    if not probe_ok:
        row["error"] = f"PROBE_FAIL: {probe_msg}"
        evidence_items.append(
            {
                "source": "concurrency_stress",
                "load_level": level_name,
                "task_index": task_index,
                "pair_name": pair_name,
                "stage": "probe",
                "message": probe_msg,
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
    nonce = str(uuid.uuid4())
    auth_details = build_authorization_details(
        detail_type="vp_presentation",
        actions=["present"],
        locations=[f"http://localhost:{holder_port}"],
        datatypes=["AgentIdentityCredential", "AgentToolsetCredential"],
        identifier="holder-auth",
        privileges=["identity", "toolset"],
    )
    payload = with_request_envelope(
        {
            "nonce": nonce,
            "verifier_did": wallet.did,
            "type": "AuthRequest",
            "requiredVcTypes": ["AgentIdentityCredential", "AgentToolsetCredential"],
        },
        resource="urn:dagg:holder:auth",
        action="authenticate",
        nonce=nonce,
        authorization_details=auth_details,
    )
    payload["verifier_signature"] = "0xdeadbeef"
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


def build_auth_request_payload(
    verifier_did: str,
    holder_port: int,
    nonce: str | None = None,
    required_vc_types: list[str] | None = None,
) -> tuple[dict[str, Any], str]:
    """
    功能：
    生成标准 Auth 请求体（含请求封套字段），供正负例复用。

    参数：
    verifier_did (str): 发起方 DID。
    holder_port (int): 目标 Holder 端口。
    nonce (str | None): 可选固定 nonce。
    required_vc_types (list[str] | None): 期望出示的 VC 类型列表。

    返回值：
    tuple[dict[str, Any], str]:
    (封装后的请求体, 实际 nonce)。
    """
    actual_nonce = str(nonce or uuid.uuid4())
    required_types = list(required_vc_types or ["AgentIdentityCredential", "AgentToolsetCredential"])
    auth_details = build_authorization_details(
        detail_type="vp_presentation",
        actions=["present"],
        locations=[f"http://localhost:{holder_port}"],
        datatypes=required_types,
        identifier="holder-auth",
        privileges=["identity", "toolset"],
    )
    payload = with_request_envelope(
        {
            "nonce": actual_nonce,
            "verifier_did": verifier_did,
            "type": "AuthRequest",
            "requiredVcTypes": required_types,
        },
        resource="urn:dagg:holder:auth",
        action="authenticate",
        nonce=actual_nonce,
        authorization_details=auth_details,
    )
    return payload, actual_nonce


def run_unregistered_agent_negative(
    config: dict[str, Any],
    key_config: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    功能：
    执行“未注册 Agent”负例，校验其在注册表中的 isRegistered=False。

    参数：
    config (dict[str, Any]): fullflow 运行配置（读取 discovery.registry_address）。
    key_config (dict[str, Any]): 密钥配置（读取 api_url）。

    返回值：
    tuple[dict[str, Any], list[dict[str, Any]]]:
    (负例指标行, 证据列表)。
    """
    rpc_url = str(key_config.get("api_url", "")).strip()
    registry_address = str(config.get("discovery", {}).get("registry_address", "")).strip()
    if not rpc_url or not registry_address:
        row = {
            "scenario": "negative",
            "case_id": "verification_negative_unregistered_agent",
            "capability_id": "verification.unregistered_agent_reject",
            "negative_case": "unregistered_agent",
            "status": "failed",
            "error": "缺少 api_url 或 discovery.registry_address",
        }
        evidence = [
            {
                "source": "negative_test",
                "case": "unregistered_agent",
                "rpc_url": rpc_url,
                "registry_address": registry_address,
                "timestamp": time.time(),
            }
        ]
        return row, evidence

    registry_abi = [
        {
            "inputs": [{"internalType": "string", "name": "", "type": "string"}],
            "name": "getAgentByDID",
            "outputs": [
                {"internalType": "string", "name": "did", "type": "string"},
                {"internalType": "address", "name": "admin", "type": "address"},
                {"internalType": "address", "name": "op", "type": "address"},
                {"internalType": "string", "name": "cid", "type": "string"},
                {"internalType": "uint256", "name": "stakeAmount", "type": "uint256"},
                {"internalType": "uint256", "name": "initScore", "type": "uint256"},
                {"internalType": "uint256", "name": "accumulatedPenalty", "type": "uint256"},
                {"internalType": "bool", "name": "isRegistered", "type": "bool"},
                {"internalType": "bool", "name": "slashed", "type": "bool"},
                {"internalType": "uint256", "name": "lastMisconductTimestamp", "type": "uint256"},
            ],
            "stateMutability": "view",
            "type": "function",
        }
    ]

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    temp_account = w3.eth.account.create(extra_entropy=f"unregistered-{time.time_ns()}")
    verifier_did = f"did:ethr:sepolia:{temp_account.address}"
    registry_contract = w3.eth.contract(address=Web3.to_checksum_address(registry_address), abi=registry_abi)
    is_registered = False
    call_error = ""
    fatal_error = False
    try:
        agent_tuple = registry_contract.functions.getAgentByDID(verifier_did).call()
        is_registered = bool(agent_tuple[7]) if isinstance(agent_tuple, (list, tuple)) and len(agent_tuple) > 7 else False
    except Exception as exc:  # noqa: BLE001
        call_error = str(exc)
        lowered = call_error.lower()
        if "execution reverted" in lowered:
            is_registered = False
        else:
            fatal_error = True

    passed = (not is_registered) and (not fatal_error)
    row = {
        "scenario": "negative",
        "case_id": "verification_negative_unregistered_agent",
        "capability_id": "verification.unregistered_agent_reject",
        "negative_case": "unregistered_agent",
        "expected_result": "isRegistered=False",
        "actual_result": (
            f"isRegistered={is_registered}"
            if not call_error
            else f"isRegistered={is_registered} (call_error={call_error})"
        ),
        "status": "passed" if passed else "failed",
        "error": "" if passed else (call_error or "未注册 Agent 在注册表中意外存在"),
    }
    evidence = [
        {
            "source": "negative_test",
            "case": "unregistered_agent",
            "verifier_did": verifier_did,
            "registry_address": registry_address,
            "is_registered": is_registered,
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


def _run_single_mcp_abuse_request(
    *,
    wallet: IdentityWallet,
    pair_name: str,
    holder_port: int,
    case_cfg: dict[str, str],
    request_index: int,
) -> dict[str, Any]:
    """
    功能：
    发起单条 A2A + MCP 越权请求，记录是否被 403 拦截。

    参数：
    wallet (IdentityWallet): 发起请求的钱包。
    pair_name (str): 对组名称。
    holder_port (int): 目标 Holder 端口。
    case_cfg (dict[str, str]): 越权场景定义（case_id/resource/action）。
    request_index (int): 并发请求序号。

    返回值：
    dict[str, Any]: 单条请求执行结果。
    """
    case_id = str(case_cfg.get("case_id", "mcp_abuse_unknown"))
    resource = str(case_cfg.get("resource", "")).strip()
    action = str(case_cfg.get("action", "")).strip()
    target_uri = f"http://localhost:{holder_port}/a2a/message/send"
    started = time.perf_counter()
    try:
        auth_details = build_authorization_details(
            detail_type="tool-execution",
            actions=[action],
            locations=[f"http://localhost:{holder_port}"],
            datatypes=["tool-call"],
            identifier=case_id,
            privileges=["tool"],
        )
        payload = with_request_envelope(
            {
                "senderDid": wallet.did,
                "message": f"{case_id}-idx-{request_index}",
                "toolCall": {
                    "providerProtocol": "mcp",
                    "serverId": "official-time",
                    "toolName": "get_current_time",
                    "arguments": {"timezone": "Asia/Shanghai"},
                },
            },
            resource=resource,
            action=action,
            request_id=str(uuid.uuid4()),
            authorization_details=auth_details,
        )
        serialized = build_request_signature_payload(
            payload,
            http_method="POST",
            target_uri=target_uri,
        )
        payload["senderSignature"] = wallet.sign_message(serialized)
        resp = requests.post(target_uri, json=payload, timeout=30)
        elapsed = time.perf_counter() - started
        blocked = resp.status_code == 403
        return {
            "pair_name": pair_name,
            "case_id": case_id,
            "request_index": request_index,
            "holder_port": holder_port,
            "status_code": resp.status_code,
            "blocked": blocked,
            "latency_seconds": elapsed,
            "error": "" if blocked else f"期望403，实际{resp.status_code}",
        }
    except Exception as exc:  # noqa: BLE001
        elapsed = time.perf_counter() - started
        return {
            "pair_name": pair_name,
            "case_id": case_id,
            "request_index": request_index,
            "holder_port": holder_port,
            "status_code": 0,
            "blocked": False,
            "latency_seconds": elapsed,
            "error": str(exc),
        }


def run_mcp_abuse_concurrency_negative(
    pairs: list[dict[str, Any]],
    key_config: dict[str, Any],
    requests_per_pair: int = 5,
    max_workers: int = 8,
    load_level: str = "L1",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    功能：
    执行并发 MCP 越权联动负例（非法 action / 非法 resource）。

    参数：
    pairs (list[dict[str, Any]]): fullflow 配置中的验证对组。
    key_config (dict[str, Any]): agents_4_key 配置。
    requests_per_pair (int): 每个 pair 每类场景并发请求数。
    max_workers (int): 并发线程数上限。

    返回值：
    tuple[dict[str, Any], list[dict[str, Any]]]:
    (汇总指标行, 证据列表)。
    """
    level_name = str(load_level or "L1")
    level_key = to_level_key(level_name)

    if requests_per_pair <= 0:
        row = {
            "scenario": "negative",
            "case_id": f"verification_negative_mcp_abuse_concurrency_{level_key}",
            "capability_id": "verification.mcp_abuse_concurrency_reject",
            "negative_case": "mcp_abuse_concurrency",
            "load_level": level_name,
            "load_level_key": level_key,
            "status": "passed",
            "total_requests": 0,
            "blocked_requests": 0,
            "pass_rate": 1.0,
            "avg_latency_seconds": 0.0,
            "max_latency_seconds": 0.0,
            "error": "",
        }
        return row, []

    abuse_cases = [
        {
            "case_id": "mcp_abuse_unauthorized_action",
            "resource": "resource:time:current",
            "action": "execute",
        },
        {
            "case_id": "mcp_abuse_unauthorized_resource",
            "resource": "resource:system:admin",
            "action": "query",
        },
    ]

    wallet_map: dict[str, IdentityWallet] = {}
    for pair in pairs:
        verifier_role = str(pair.get("verifier_role", "")).strip()
        if not verifier_role:
            continue
        if verifier_role in wallet_map:
            continue
        wallet_map[verifier_role] = IdentityWallet(verifier_role, override_config=key_config)

    results: list[dict[str, Any]] = []
    futures = []
    worker_count = max(1, int(max_workers))
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        for pair in pairs:
            pair_name = str(pair.get("name", "pair"))
            verifier_role = str(pair.get("verifier_role", "")).strip()
            holder_port = int(pair.get("holder_port", 0) or 0)
            wallet = wallet_map.get(verifier_role)
            if wallet is None or holder_port <= 0:
                continue
            for case_cfg in abuse_cases:
                for request_idx in range(requests_per_pair):
                    futures.append(
                        pool.submit(
                            _run_single_mcp_abuse_request,
                            wallet=wallet,
                            pair_name=pair_name,
                            holder_port=holder_port,
                            case_cfg=case_cfg,
                            request_index=request_idx,
                        )
                    )

        for future in as_completed(futures):
            results.append(future.result())

    total_requests = len(results)
    blocked_requests = sum(1 for item in results if bool(item.get("blocked")))
    latencies = [float(item.get("latency_seconds", 0.0)) for item in results if float(item.get("latency_seconds", 0.0)) > 0]
    avg_latency = (sum(latencies) / len(latencies)) if latencies else 0.0
    max_latency = max(latencies) if latencies else 0.0
    pass_rate = (float(blocked_requests) / float(total_requests)) if total_requests > 0 else 0.0
    passed = (total_requests > 0) and (blocked_requests == total_requests)

    failed_samples = [item for item in results if not bool(item.get("blocked"))][:5]
    evidence_items = [
        {
            "source": "negative_test",
            "case": "mcp_abuse_concurrency",
            "load_level": level_name,
            "total_requests": total_requests,
            "blocked_requests": blocked_requests,
            "pass_rate": pass_rate,
            "failed_samples": failed_samples,
            "timestamp": time.time(),
        }
    ]
    row = {
        "scenario": "negative",
        "case_id": f"verification_negative_mcp_abuse_concurrency_{level_key}",
        "capability_id": "verification.mcp_abuse_concurrency_reject",
        "negative_case": "mcp_abuse_concurrency",
        "load_level": level_name,
        "load_level_key": level_key,
        "status": "passed" if passed else "failed",
        "total_requests": total_requests,
        "blocked_requests": blocked_requests,
        "pass_rate": round(pass_rate, 4),
        "avg_latency_seconds": round(avg_latency, 6),
        "max_latency_seconds": round(max_latency, 6),
        "error": "" if passed else f"并发越权请求存在漏拦截: {blocked_requests}/{total_requests}",
    }
    return row, evidence_items


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
    payload, nonce = build_auth_request_payload(
        verifier_did=wallet.did,
        holder_port=holder_port,
    )
    serialized = build_request_signature_payload(
        payload,
        http_method="POST",
        target_uri=f"http://localhost:{holder_port}/auth",
    )
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


def run_expired_vc_negative(
    holder_port: int,
    verifier_role: str,
    key_config: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    功能：
    执行“过期 VC”负例，期望 verify_vp 失败。

    参数：
    holder_port (int): 目标 Holder 端口。
    verifier_role (str): Verifier 角色名。
    key_config (dict[str, Any]): agents_4_key 配置。

    返回值：
    tuple[dict[str, Any], list[dict[str, Any]]]:
    (负例指标行, 证据列表)。
    """
    ok, msg, vp, nonce = request_valid_auth_vp(
        holder_port=holder_port,
        verifier_role=verifier_role,
        key_config=key_config,
    )
    if not ok or vp is None:
        row = {
            "scenario": "negative",
            "case_id": "verification_negative_expired_vc",
            "capability_id": "verification.expired_vc_reject",
            "negative_case": "expired_vc",
            "status": "failed",
            "error": f"负例前置 auth 失败: {msg}",
        }
        evidence = [
            {
                "source": "negative_test",
                "case": "expired_vc",
                "message": msg,
                "timestamp": time.time(),
            }
        ]
        return row, evidence

    tampered_vp = copy.deepcopy(vp)
    vc_list = tampered_vp.get("verifiableCredential")
    if not isinstance(vc_list, list) or not vc_list:
        row = {
            "scenario": "negative",
            "case_id": "verification_negative_expired_vc",
            "capability_id": "verification.expired_vc_reject",
            "negative_case": "expired_vc",
            "status": "failed",
            "error": "VP 中缺少可篡改 VC",
        }
        evidence = [
            {
                "source": "negative_test",
                "case": "expired_vc",
                "message": "VP 中缺少 verifiableCredential",
                "timestamp": time.time(),
            }
        ]
        return row, evidence

    target_vc = vc_list[0]
    if not isinstance(target_vc, dict):
        row = {
            "scenario": "negative",
            "case_id": "verification_negative_expired_vc",
            "capability_id": "verification.expired_vc_reject",
            "negative_case": "expired_vc",
            "status": "failed",
            "error": "VC 结构非法",
        }
        evidence = [
            {
                "source": "negative_test",
                "case": "expired_vc",
                "message": "VC 结构非法",
                "timestamp": time.time(),
            }
        ]
        return row, evidence

    origin_valid_until = str(target_vc.get("validUntil", ""))
    target_vc["validUntil"] = "2000-01-01T00:00:00Z"

    holder_did = tampered_vp.get("holder")
    if isinstance(holder_did, dict):
        holder_did = holder_did.get("id")
    holder_signer: IdentityWallet | None = None
    for role_name in key_config.get("accounts", {}):
        role_text = str(role_name)
        if not role_text.endswith("_op"):
            continue
        try:
            wallet = IdentityWallet(role_text, override_config=key_config)
        except Exception:
            continue
        if wallet.did == holder_did:
            holder_signer = wallet
            break

    if holder_signer is not None:
        vp_unsigned = copy.deepcopy(tampered_vp)
        vp_unsigned.pop("proof", None)
        serialized_vp = json.dumps(vp_unsigned, sort_keys=True, separators=(",", ":"))
        proof_obj = tampered_vp.get("proof") if isinstance(tampered_vp.get("proof"), dict) else {}
        proof_obj["jws"] = holder_signer.sign_message(serialized_vp)
        tampered_vp["proof"] = proof_obj

    validator = DIDValidator()
    valid, reason = validator.verify_vp(tampered_vp, expected_nonce=nonce)
    passed = (not valid) and ("过期" in reason or "expired" in reason.lower())
    row = {
        "scenario": "negative",
        "case_id": "verification_negative_expired_vc",
        "capability_id": "verification.expired_vc_reject",
        "negative_case": "expired_vc",
        "expected_result": "verify_vp=False",
        "actual_result": f"verify_vp={valid}",
        "validator_reason": reason,
        "status": "passed" if passed else "failed",
        "error": "" if passed else "过期VC未被拒绝",
    }
    evidence = [
        {
            "source": "negative_test",
            "case": "expired_vc",
            "original_valid_until": origin_valid_until,
            "tampered_valid_until": target_vc.get("validUntil"),
            "validator_reason": reason,
            "timestamp": time.time(),
        }
    ]
    return row, evidence


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
    mcp_abuse_cfg = dict(verify_cfg.get("mcp_abuse_concurrency", {}))
    mcp_abuse_enabled = bool(mcp_abuse_cfg.get("enabled", True))
    mcp_abuse_requests_per_pair = int(mcp_abuse_cfg.get("requests_per_pair", 5))
    mcp_abuse_max_workers = int(
        mcp_abuse_cfg.get(
            "max_workers",
            max(4, len(pairs) * max(1, mcp_abuse_requests_per_pair)),
        )
    )
    mcp_abuse_min_pass_rate = float(mcp_abuse_cfg.get("min_pass_rate", 1.0))
    mcp_abuse_raw_levels = mcp_abuse_cfg.get("matrix_levels", [])
    mcp_abuse_levels: list[dict[str, Any]] = []
    if isinstance(mcp_abuse_raw_levels, list):
        for idx, item in enumerate(mcp_abuse_raw_levels):
            if not isinstance(item, dict):
                continue
            level_name = str(item.get("name", f"L{idx + 1}")).strip() or f"L{idx + 1}"
            level_requests = int(item.get("requests_per_pair", mcp_abuse_requests_per_pair))
            level_workers = int(item.get("max_workers", mcp_abuse_max_workers))
            level_threshold = float(item.get("min_pass_rate", mcp_abuse_min_pass_rate))
            mcp_abuse_levels.append(
                {
                    "name": level_name,
                    "requests_per_pair": max(1, level_requests),
                    "max_workers": max(1, level_workers),
                    "min_pass_rate": max(0.0, min(1.0, level_threshold)),
                }
            )
    if not mcp_abuse_levels:
        mcp_abuse_levels.append(
            {
                "name": "L1",
                "requests_per_pair": max(1, mcp_abuse_requests_per_pair),
                "max_workers": max(1, mcp_abuse_max_workers),
                "min_pass_rate": max(0.0, min(1.0, mcp_abuse_min_pass_rate)),
            }
        )

    concurrency_cfg = dict(verify_cfg.get("concurrency_stress", {}))
    concurrency_enabled = bool(concurrency_cfg.get("enabled", True))
    concurrency_tasks_per_pair = int(concurrency_cfg.get("tasks_per_pair", 2))
    concurrency_max_workers = int(
        concurrency_cfg.get(
            "max_workers",
            max(4, len(pairs) * max(1, concurrency_tasks_per_pair)),
        )
    )
    concurrency_min_pass_rate = float(concurrency_cfg.get("min_pass_rate", 0.95))
    concurrency_raw_levels = concurrency_cfg.get("matrix_levels", [])
    concurrency_levels: list[dict[str, Any]] = []
    if isinstance(concurrency_raw_levels, list):
        for idx, item in enumerate(concurrency_raw_levels):
            if not isinstance(item, dict):
                continue
            level_name = str(item.get("name", f"L{idx + 1}")).strip() or f"L{idx + 1}"
            level_tasks = int(item.get("tasks_per_pair", concurrency_tasks_per_pair))
            level_workers = int(item.get("max_workers", concurrency_max_workers))
            level_threshold = float(item.get("min_pass_rate", concurrency_min_pass_rate))
            concurrency_levels.append(
                {
                    "name": level_name,
                    "tasks_per_pair": max(1, level_tasks),
                    "max_workers": max(1, level_workers),
                    "min_pass_rate": max(0.0, min(1.0, level_threshold)),
                }
            )
    if not concurrency_levels:
        concurrency_levels.append(
            {
                "name": "L1",
                "tasks_per_pair": max(1, concurrency_tasks_per_pair),
                "max_workers": max(1, concurrency_max_workers),
                "min_pass_rate": max(0.0, min(1.0, concurrency_min_pass_rate)),
            }
        )

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
                for first in failed_rows:
                    emit_progress(
                        f"正向用例失败（继续）: pair={first.get('pair_name')} round={first.get('round')} error={first.get('error')}"
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

        if concurrency_enabled:
            stress_summaries: list[dict[str, Any]] = []
            for level in concurrency_levels:
                level_name = str(level.get("name", "L1"))
                level_tasks = int(level.get("tasks_per_pair", concurrency_tasks_per_pair))
                level_workers = int(level.get("max_workers", concurrency_max_workers))
                level_threshold = float(level.get("min_pass_rate", concurrency_min_pass_rate))
                emit_progress(
                    "执行并发压测: positive_concurrency_stress "
                    f"[{level_name}] "
                    f"(tasks_per_pair={level_tasks}, workers={level_workers}, min_pass_rate={level_threshold:.2f})"
                )
                stress_summary, stress_task_rows, stress_evidence = run_concurrency_stress_positive(
                    pairs=pairs,
                    key_config=key_config,
                    runtime_base_dir=runtime_base_dir,
                    tasks_per_pair=level_tasks,
                    max_workers=level_workers,
                    min_pass_rate=level_threshold,
                    load_level=level_name,
                )
                phase_metrics.extend(stress_task_rows)
                phase_metrics.append(stress_summary)
                stress_summaries.append(stress_summary)
                evidence_items.extend(stress_evidence)
                case_assertions.append(
                    build_case_assertion(
                        case_id=str(stress_summary.get("case_id", "verification_concurrency_stress_summary")),
                        capability_id=str(stress_summary.get("capability_id", "verification.concurrency_stress_positive")),
                        expected=(
                            f"[{level_name}] 并发压测通过率 >= {float(stress_summary.get('min_pass_rate', level_threshold)):.2f}"
                        ),
                        actual=(
                            f"level={level_name} "
                            f"status={stress_summary.get('status')} "
                            f"pass_rate={stress_summary.get('pass_rate')} "
                            f"passed={stress_summary.get('passed_tasks', 0)}/{stress_summary.get('total_tasks', 0)} "
                            f"p95={stress_summary.get('p95_duration_seconds', 0)}s "
                            f"tps={stress_summary.get('throughput_tps', 0)}"
                        ),
                        passed=str(stress_summary.get("status")) == "passed",
                        error=str(stress_summary.get("error", "")),
                    )
                )
                if str(stress_summary.get("status")) != "passed":
                    emit_progress(
                        f"并发压测[{level_name}] 未达阈值: {stress_summary.get('error', '')}"
                    )

            stress_total = len(stress_summaries)
            stress_passed = sum(1 for row in stress_summaries if str(row.get("status")) == "passed")
            matrix_summary = {
                "scenario": "concurrency_stress_matrix_summary",
                "case_id": "verification_concurrency_stress_matrix_summary",
                "capability_id": "verification.concurrency_stress_positive",
                "status": "passed" if stress_passed == stress_total and stress_total > 0 else "failed",
                "levels_total": stress_total,
                "levels_passed": stress_passed,
                "levels_failed": max(stress_total - stress_passed, 0),
                "levels": ",".join(str(row.get("load_level", "")) for row in stress_summaries),
            }
            phase_metrics.append(matrix_summary)
            case_assertions.append(
                build_case_assertion(
                    case_id="verification_concurrency_stress_matrix_summary",
                    capability_id="verification.concurrency_stress_positive",
                    expected=f"并发压测矩阵全部通过（{stress_total}档）",
                    actual=(
                        f"passed={stress_passed}/{stress_total} "
                        f"levels={matrix_summary.get('levels', '')}"
                    ),
                    passed=stress_total > 0 and stress_passed == stress_total,
                    error="" if stress_total > 0 and stress_passed == stress_total else "存在未达标档位",
                )
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
            emit_progress(f"负例失败（继续）: fake_signature_auth -> {neg_row_a.get('error')}")

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
            emit_progress(f"负例失败（继续）: context_mismatch -> {neg_row_b.get('error')}")

        emit_progress("执行负例: unregistered_agent")
        neg_row_unreg, neg_evidence_unreg = run_unregistered_agent_negative(
            config=config,
            key_config=key_config,
        )
        phase_metrics.append(neg_row_unreg)
        evidence_items.extend(neg_evidence_unreg)
        case_assertions.append(
            build_case_assertion(
                case_id=str(neg_row_unreg.get("case_id", "verification_negative_unregistered_agent")),
                capability_id=str(neg_row_unreg.get("capability_id", "verification.unregistered_agent_reject")),
                expected="未注册 Agent 在注册表中应为 isRegistered=False",
                actual=f"status={neg_row_unreg.get('status')} result={neg_row_unreg.get('actual_result', '')}",
                passed=str(neg_row_unreg.get("status")) == "passed",
                error=str(neg_row_unreg.get("error", "")),
            )
        )
        if str(neg_row_unreg.get("status")) != "passed":
            emit_progress(f"负例失败（继续）: unregistered_agent -> {neg_row_unreg.get('error')}")

        emit_progress("执行负例: expired_vc")
        neg_row_expired, neg_evidence_expired = run_expired_vc_negative(
            holder_port=holder_start_port,
            verifier_role=negative_verifier_role,
            key_config=key_config,
        )
        phase_metrics.append(neg_row_expired)
        evidence_items.extend(neg_evidence_expired)
        case_assertions.append(
            build_case_assertion(
                case_id=str(neg_row_expired.get("case_id", "verification_negative_expired_vc")),
                capability_id=str(neg_row_expired.get("capability_id", "verification.expired_vc_reject")),
                expected="过期 VC 应被 verify_vp 拒绝",
                actual=f"status={neg_row_expired.get('status')} result={neg_row_expired.get('actual_result', '')}",
                passed=str(neg_row_expired.get("status")) == "passed",
                error=str(neg_row_expired.get("error", "")),
            )
        )
        if str(neg_row_expired.get("status")) != "passed":
            emit_progress(f"负例失败（继续）: expired_vc -> {neg_row_expired.get('error')}")

        if mcp_abuse_enabled:
            abuse_rows: list[dict[str, Any]] = []
            for level in mcp_abuse_levels:
                level_name = str(level.get("name", "L1"))
                level_requests = int(level.get("requests_per_pair", mcp_abuse_requests_per_pair))
                level_workers = int(level.get("max_workers", mcp_abuse_max_workers))
                level_threshold = float(level.get("min_pass_rate", mcp_abuse_min_pass_rate))
                emit_progress(
                    "执行负例: mcp_abuse_concurrency "
                    f"[{level_name}] "
                    f"(requests_per_pair={level_requests}, workers={level_workers}, min_pass_rate={level_threshold:.2f})"
                )
                neg_row_mcp_abuse, neg_evidence_mcp_abuse = run_mcp_abuse_concurrency_negative(
                    pairs=pairs,
                    key_config=key_config,
                    requests_per_pair=level_requests,
                    max_workers=level_workers,
                    load_level=level_name,
                )
                phase_metrics.append(neg_row_mcp_abuse)
                abuse_rows.append(neg_row_mcp_abuse)
                evidence_items.extend(neg_evidence_mcp_abuse)
                pass_rate_actual = float(neg_row_mcp_abuse.get("pass_rate", 0.0))
                level_passed = str(neg_row_mcp_abuse.get("status")) == "passed" and pass_rate_actual >= level_threshold
                case_assertions.append(
                    build_case_assertion(
                        case_id=str(
                            neg_row_mcp_abuse.get(
                                "case_id",
                                "verification_negative_mcp_abuse_concurrency",
                            )
                        ),
                        capability_id=str(
                            neg_row_mcp_abuse.get(
                                "capability_id",
                                "verification.mcp_abuse_concurrency_reject",
                            )
                        ),
                        expected=f"[{level_name}] 并发 MCP 越权拦截率 >= {level_threshold:.2f}",
                        actual=(
                            f"level={level_name} "
                            f"status={neg_row_mcp_abuse.get('status')} "
                            f"blocked={neg_row_mcp_abuse.get('blocked_requests', 0)}/"
                            f"{neg_row_mcp_abuse.get('total_requests', 0)} "
                            f"pass_rate={pass_rate_actual:.4f}"
                        ),
                        passed=level_passed,
                        error=str(neg_row_mcp_abuse.get("error", "")),
                    )
                )
                if not level_passed:
                    emit_progress(f"负例失败（继续）: mcp_abuse_concurrency[{level_name}] -> {neg_row_mcp_abuse.get('error')}")

            abuse_total = len(abuse_rows)
            abuse_passed = sum(1 for row in abuse_rows if str(row.get("status")) == "passed")
            phase_metrics.append(
                {
                    "scenario": "negative_matrix_summary",
                    "case_id": "verification_negative_mcp_abuse_matrix_summary",
                    "capability_id": "verification.mcp_abuse_concurrency_reject",
                    "negative_case": "mcp_abuse_concurrency",
                    "status": "passed" if abuse_total > 0 and abuse_passed == abuse_total else "failed",
                    "levels_total": abuse_total,
                    "levels_passed": abuse_passed,
                    "levels_failed": max(abuse_total - abuse_passed, 0),
                }
            )

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
            emit_progress(f"负例失败（继续）: tampered_vp_challenge -> {neg_row_c.get('error')}")

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
            emit_progress(f"负例失败（继续）: tampered_vp_signature -> {neg_row_d.get('error')}")
    finally:
        emit_progress("停止 Issuer/Holder 进程")
        terminate_processes(processes)

    return {
        "phase_metrics": phase_metrics,
        "evidence_items": evidence_items,
        "round_summaries": round_summaries,
        "case_assertions": case_assertions,
    }
