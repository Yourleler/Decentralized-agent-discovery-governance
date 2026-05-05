"""
多规模并发测试自动化脚本。

用途：按顺序运行多个规模（如 1v1, 3v3, 5v5, 10v10）的联动压测：
  1) P2P 认证性能压测（Auth/Probe/Context）
  2) A2A + MCP 并发越权请求压测（非法 action / 非法 resource）
汇总为对比 CSV + 规模分析图表，用于论文中“不同规模下的响应延迟与互操作性/安全性”章节。

前置条件：
  1. 已通过 setup_agents_N.py 生成目标规模的账户（verifiers_key.json / holders_key.json）
  2. Issuer 服务已启动（_ops_services/issuer_server.py）

运行方式：
  python _experiments/run_scale_tests.py

说明：
  - 每个规模会先启动对应数量的 Holder，再启动对应数量的 Verifier 并发测试
  - Verifier 测试结束后，会在同一规模下并发发起 MCP 越权请求压测
  - 每个规模完成后收集指标并清理进程
  - 最终输出对比报表
"""

import sys
import os
import json
import csv
import time
import subprocess
import signal
import multiprocessing
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

# 定位项目根目录
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = current_dir
while not os.path.exists(os.path.join(project_root, "infrastructure")):
    parent = os.path.dirname(project_root)
    if parent == project_root:
        break
    project_root = parent
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agents.verifier.runtime import VerifierRuntime
from infrastructure.wallet import IdentityWallet
from interop.request_policy import (
    build_authorization_details,
    build_request_signature_payload,
    with_request_envelope,
)

# === 测试规模配置 ===
SCALE_TARGETS = [1, 3, 5, 10]  # 并发对数
MCP_ABUSE_CASES = [
    {
        "case_id": "mcp_unauthorized_action",
        "resource": "resource:time:current",
        "action": "execute",
        "description": "合法工具 + 非法动作 execute，应被 403 拒绝",
    },
    {
        "case_id": "mcp_unauthorized_resource",
        "resource": "resource:system:admin",
        "action": "query",
        "description": "合法动作 + 非法资源，应被 403 拒绝",
    },
]

# === 路径 ===
VERIFIERS_KEY_PATH = os.path.join(project_root, "data", "verifiers_key.json")
HOLDERS_KEY_PATH = os.path.join(project_root, "data", "holders_key.json")
HOLDER_RUNTIME_SCRIPT = os.path.join(project_root, "agents", "holder", "runtime.py")
RESULT_DIR = os.path.join(current_dir, "result")
SCALE_CSV_PATH = os.path.join(RESULT_DIR, "scale_comparison.csv")


def ensure_result_dir():
    """确保结果目录存在"""
    os.makedirs(RESULT_DIR, exist_ok=True)


def load_key_config(path: str) -> dict:
    """加载密钥配置文件"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_sorted_roles(accounts: dict, role_prefix: str, suffix: str = "_op") -> list:
    """筛选并排序指定角色"""
    roles = [k for k in accounts.keys() if suffix in k and role_prefix in k]
    try:
        roles.sort(key=lambda x: int(x.split("_")[1]))
    except (ValueError, IndexError):
        roles.sort()
    return roles


def start_holders(num_holders: int) -> list:
    """启动指定数量的 Holder 进程"""
    holders_config = load_key_config(HOLDERS_KEY_PATH)
    accounts = holders_config.get("accounts", {})
    holder_roles = get_sorted_roles(accounts, "holder")[:num_holders]

    processes = []
    for i, role in enumerate(holder_roles):
        port = 5000 + i
        cmd = [sys.executable, HOLDER_RUNTIME_SCRIPT, str(port), role, HOLDERS_KEY_PATH]
        try:
            p = subprocess.Popen(
                cmd,
                cwd=project_root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            processes.append(p)
            print(f"  [Holder] 启动 {role} 端口 {port} (PID: {p.pid})")
        except Exception as e:
            print(f"  [Holder] 启动失败 {role}: {e}")
    return processes


def stop_processes(processes: list):
    """终止所有子进程"""
    for p in processes:
        if p.poll() is None:
            p.terminate()
    for p in processes:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


def run_verifier_worker(name, config, role_name, stats_queue, barrier, target_url):
    """Verifier 工作进程"""
    try:
        verifier = VerifierRuntime(
            role_name=role_name,
            config=config,
            instance_name=name,
            target_holder_url=target_url,
        )
        verifier.run(max_turns=12, barrier=barrier, stats_queue=stats_queue)
    except Exception as e:
        print(f"  [{name}] 异常: {e}")


def _build_mcp_abuse_payload(
    *,
    holder_port: int,
    wallet: IdentityWallet,
    case_cfg: dict,
) -> tuple[dict, str]:
    """
    构造 A2A + MCP 越权请求体并完成签名。

    参数：
      holder_port: 目标 Holder 端口
      wallet: 发起请求的钱包
      case_cfg: 越权场景配置（resource/action/case_id）

    返回：
      (signed_payload, target_uri)
    """
    holder_url = f"http://localhost:{holder_port}"
    target_uri = f"{holder_url}/a2a/message/send"
    auth_details = build_authorization_details(
        detail_type="tool-execution",
        actions=[str(case_cfg.get("action", "")).strip()],
        locations=[holder_url],
        datatypes=["tool-call"],
        identifier=str(case_cfg.get("case_id", "mcp-abuse")),
        privileges=["tool"],
    )
    payload = with_request_envelope(
        {
            "senderDid": wallet.did,
            "message": f"abuse_case={case_cfg.get('case_id', '')}",
            "toolCall": {
                "providerProtocol": "mcp",
                "serverId": "official-time",
                "toolName": "get_current_time",
                "arguments": {"timezone": "Asia/Shanghai"},
            },
        },
        resource=str(case_cfg.get("resource", "")).strip(),
        action=str(case_cfg.get("action", "")).strip(),
        request_id=str(uuid.uuid4()),
        authorization_details=auth_details,
    )
    signed_payload = dict(payload)
    serialized = build_request_signature_payload(
        payload,
        http_method="POST",
        target_uri=target_uri,
    )
    signed_payload["senderSignature"] = wallet.sign_message(serialized)
    return signed_payload, target_uri


def _run_single_mcp_abuse_case(
    *,
    scale: int,
    holder_port: int,
    verifier_role: str,
    key_config: dict,
    case_cfg: dict,
) -> dict:
    """
    执行单条 MCP 越权请求并记录结果。
    """
    t0 = time.perf_counter()
    row = {
        "scale": scale,
        "holder_port": holder_port,
        "verifier_role": verifier_role,
        "case_id": str(case_cfg.get("case_id", "")),
        "resource": str(case_cfg.get("resource", "")),
        "action": str(case_cfg.get("action", "")),
        "expected_http_status": 403,
        "actual_http_status": 0,
        "latency_seconds": 0.0,
        "passed": False,
        "error": "",
    }
    try:
        wallet = IdentityWallet(verifier_role, override_config=key_config)
        payload, target_uri = _build_mcp_abuse_payload(
            holder_port=holder_port,
            wallet=wallet,
            case_cfg=case_cfg,
        )
        resp = requests.post(target_uri, json=payload, timeout=30)
        elapsed = time.perf_counter() - t0
        row["latency_seconds"] = round(elapsed, 6)
        row["actual_http_status"] = int(resp.status_code)
        denied = resp.status_code == 403
        row["passed"] = bool(denied)
        if not denied:
            row["error"] = f"期望403，实际{resp.status_code}"
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        row["latency_seconds"] = round(elapsed, 6)
        row["error"] = str(exc)
        row["passed"] = False
    return row


def run_mcp_abuse_stress(num_pairs: int, verifier_roles: list, verifier_config: dict) -> tuple[list, dict]:
    """
    执行“多规模 + MCP 并发越权”联动压测。

    参数：
      num_pairs: 当前规模
      verifier_roles: 本规模使用的 verifier 角色列表
      verifier_config: verifier 密钥配置

    返回：
      (rows, summary)
    """
    rows = []
    futures = []
    max_workers = max(1, num_pairs * max(1, len(MCP_ABUSE_CASES)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for i, role in enumerate(verifier_roles):
            holder_port = 5000 + i
            for case_cfg in MCP_ABUSE_CASES:
                futures.append(
                    pool.submit(
                        _run_single_mcp_abuse_case,
                        scale=num_pairs,
                        holder_port=holder_port,
                        verifier_role=role,
                        key_config=verifier_config,
                        case_cfg=case_cfg,
                    )
                )
        for fut in as_completed(futures):
            rows.append(fut.result())

    valid_rows = [r for r in rows if r.get("latency_seconds", 0) > 0]
    total = len(rows)
    passed = sum(1 for r in rows if r.get("passed"))
    pass_rate = (passed / total) if total > 0 else 0.0
    avg_latency = sum(r["latency_seconds"] for r in valid_rows) / len(valid_rows) if valid_rows else 0.0
    max_latency = max((r["latency_seconds"] for r in valid_rows), default=0.0)

    summary = {
        "mcp_abuse_total": total,
        "mcp_abuse_passed": passed,
        "mcp_abuse_pass_rate": round(pass_rate, 4),
        "mcp_abuse_avg_latency": round(avg_latency, 6),
        "mcp_abuse_max_latency": round(max_latency, 6),
    }
    return rows, summary


def run_scale_test(num_pairs: int) -> dict:
    """
    执行单次规模测试。

    参数：
      num_pairs: 并发 Verifier-Holder 对数量

    返回：
      dict: 包含 scale/avg_t4/avg_t8/avg_t12/avg_total/tps/count 等指标
    """
    print(f"\n{'='*60}")
    print(f"  开始 {num_pairs}v{num_pairs} 规模测试")
    print(f"{'='*60}")

    # 1. 启动 Holders
    holder_procs = start_holders(num_pairs)
    print(f"  等待 Holder 就绪...")
    time.sleep(5)  # 预留启动时间

    # 2. 加载 Verifier 配置
    verifier_config = load_key_config(VERIFIERS_KEY_PATH)
    accounts = verifier_config.get("accounts", {})
    verifier_roles = get_sorted_roles(accounts, "verifier")[:num_pairs]

    # 3. 启动 Verifier 并发测试
    stats_queue = multiprocessing.Queue()
    start_barrier = multiprocessing.Barrier(num_pairs)
    verifier_procs = []

    for i, role in enumerate(verifier_roles):
        target_port = 5000 + i
        target_url = f"http://localhost:{target_port}"
        p_name = f"Verifier-{i+1}"

        p = multiprocessing.Process(
            target=run_verifier_worker,
            args=(p_name, verifier_config, role, stats_queue, start_barrier, target_url),
        )
        verifier_procs.append(p)
        p.start()

    # 4. 收集结果
    results = []
    collected = 0
    start_wait = time.time()

    while collected < num_pairs:
        if time.time() - start_wait > 300:
            print(f"  [WARN] 超时！已收集 {collected}/{num_pairs}")
            break
        try:
            data = stats_queue.get(timeout=10)
            results.append(data)
            collected += 1
            print(f"  已完成: {collected}/{num_pairs}")
        except Exception:
            pass

    # 5. 先清理 Verifier，再执行 MCP 越权联动压测，最后清理 Holder
    for p in verifier_procs:
        if p.is_alive():
            p.terminate()
        p.join(timeout=5)

    mcp_abuse_rows, mcp_abuse_summary = run_mcp_abuse_stress(
        num_pairs=num_pairs,
        verifier_roles=verifier_roles,
        verifier_config=verifier_config,
    )
    abuse_csv = os.path.join(RESULT_DIR, f"mcp_abuse_{num_pairs}v{num_pairs}.csv")
    abuse_fields = [
        "scale",
        "holder_port",
        "verifier_role",
        "case_id",
        "resource",
        "action",
        "expected_http_status",
        "actual_http_status",
        "latency_seconds",
        "passed",
        "error",
    ]
    try:
        with open(abuse_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=abuse_fields)
            writer.writeheader()
            writer.writerows(mcp_abuse_rows)
        print(f"  [OK] MCP 越权联动数据: {abuse_csv}")
    except Exception as e:
        print(f"  [ERR] MCP 越权联动数据保存失败: {e}")

    stop_processes(holder_procs)
    stop_processes(holder_procs)

    # 6. 计算统计指标
    if not results:
        return {
            "scale": num_pairs,
            "count": 0,
            "avg_t4": 0, "avg_t8": 0, "avg_t12": 0, "avg_total": 0,
            "max_total": 0, "tps": 0,
            "mcp_abuse_total": mcp_abuse_summary.get("mcp_abuse_total", 0),
            "mcp_abuse_passed": mcp_abuse_summary.get("mcp_abuse_passed", 0),
            "mcp_abuse_pass_rate": mcp_abuse_summary.get("mcp_abuse_pass_rate", 0),
            "mcp_abuse_avg_latency": mcp_abuse_summary.get("mcp_abuse_avg_latency", 0),
            "mcp_abuse_max_latency": mcp_abuse_summary.get("mcp_abuse_max_latency", 0),
        }

    for row in results:
        t4 = row.get("T4") or 0
        t8 = row.get("T8") or 0
        t12 = row.get("T12") or 0
        row["Total_Duration"] = t4 + t8 + t12

    valid = [r for r in results if r.get("Total_Duration", 0) > 0]
    count = len(valid)
    if count == 0:
        return {
            "scale": num_pairs,
            "count": 0,
            "avg_t4": 0, "avg_t8": 0, "avg_t12": 0, "avg_total": 0,
            "max_total": 0, "tps": 0,
            "mcp_abuse_total": mcp_abuse_summary.get("mcp_abuse_total", 0),
            "mcp_abuse_passed": mcp_abuse_summary.get("mcp_abuse_passed", 0),
            "mcp_abuse_pass_rate": mcp_abuse_summary.get("mcp_abuse_pass_rate", 0),
            "mcp_abuse_avg_latency": mcp_abuse_summary.get("mcp_abuse_avg_latency", 0),
            "mcp_abuse_max_latency": mcp_abuse_summary.get("mcp_abuse_max_latency", 0),
        }

    avg_t4 = sum(r.get("T4", 0) for r in valid) / count
    avg_t8 = sum(r.get("T8", 0) for r in valid) / count
    avg_t12 = sum(r.get("T12", 0) for r in valid) / count
    avg_total = sum(r["Total_Duration"] for r in valid) / count
    max_total = max(r["Total_Duration"] for r in valid)
    tps = count / max_total if max_total > 0 else 0

    # 保存当前规模的原始 CSV
    scale_csv = os.path.join(RESULT_DIR, f"p2p_{num_pairs}v{num_pairs}.csv")
    fieldnames = [
        "Verifier", "T1", "T2", "T3", "T4",
        "T5", "T6", "T7", "T8",
        "T9", "T10", "T11", "T12",
        "SLA_Load_Ratio", "Total_Duration",
    ]
    try:
        with open(scale_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in results:
                formatted = {
                    k: (f"{v:.4f}" if isinstance(v, float) else v)
                    for k, v in row.items()
                }
                writer.writerow(formatted)
        print(f"  [OK] 原始数据: {scale_csv}")
    except Exception as e:
        print(f"  [ERR] 保存失败: {e}")

    metric = {
        "scale": num_pairs,
        "count": count,
        "avg_t4": round(avg_t4, 4),
        "avg_t8": round(avg_t8, 4),
        "avg_t12": round(avg_t12, 4),
        "avg_total": round(avg_total, 4),
        "max_total": round(max_total, 4),
        "tps": round(tps, 4),
        "mcp_abuse_total": mcp_abuse_summary.get("mcp_abuse_total", 0),
        "mcp_abuse_passed": mcp_abuse_summary.get("mcp_abuse_passed", 0),
        "mcp_abuse_pass_rate": mcp_abuse_summary.get("mcp_abuse_pass_rate", 0),
        "mcp_abuse_avg_latency": mcp_abuse_summary.get("mcp_abuse_avg_latency", 0),
        "mcp_abuse_max_latency": mcp_abuse_summary.get("mcp_abuse_max_latency", 0),
    }
    print(
        f"  [METRIC] 规模={num_pairs}, TPS={tps:.4f}, Avg={avg_total:.4f}s, Max={max_total:.4f}s, "
        f"MCP越权拦截={metric['mcp_abuse_passed']}/{metric['mcp_abuse_total']} "
        f"({metric['mcp_abuse_pass_rate']*100:.1f}%)"
    )
    return metric


def generate_comparison_chart(metrics: list):
    """生成规模对比图表"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False
        plt.style.use("ggplot")

        scales = [m["scale"] for m in metrics if m["count"] > 0]
        tps_vals = [m["tps"] for m in metrics if m["count"] > 0]
        avg_vals = [m["avg_total"] for m in metrics if m["count"] > 0]
        t4_vals = [m["avg_t4"] for m in metrics if m["count"] > 0]
        t8_vals = [m["avg_t8"] for m in metrics if m["count"] > 0]
        t12_vals = [m["avg_t12"] for m in metrics if m["count"] > 0]
        mcp_abuse_pass_rate_vals = [float(m.get("mcp_abuse_pass_rate", 0.0)) * 100.0 for m in metrics if m["count"] > 0]
        mcp_abuse_latency_vals = [float(m.get("mcp_abuse_avg_latency", 0.0)) for m in metrics if m["count"] > 0]

        if not scales:
            print("  [WARN] 无有效数据，跳过绘图")
            return

        scale_labels = [f"{s}v{s}" for s in scales]

        # 图1: TPS vs 规模
        fig1, ax1 = plt.subplots(figsize=(8, 5), constrained_layout=True)
        bars = ax1.bar(scale_labels, tps_vals, color="#4C72B0", width=0.5)
        ax1.set_title("不同规模下的系统吞吐量 (TPS)")
        ax1.set_xlabel("并发规模")
        ax1.set_ylabel("TPS (审计/秒)")
        for bar, val in zip(bars, tps_vals):
            ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                     f"{val:.3f}", ha="center", va="bottom", fontsize=10)
        fig1.savefig(os.path.join(RESULT_DIR, "chart_scale_tps.png"), dpi=160)
        plt.close(fig1)

        # 图2: 各阶段延迟 vs 规模
        fig2, ax2 = plt.subplots(figsize=(10, 6), constrained_layout=True)
        x = range(len(scales))
        width = 0.2
        ax2.bar([i - width for i in x], t4_vals, width, label="Auth(T4)", color="#E24A33")
        ax2.bar(x, t8_vals, width, label="Probe(T8)", color="#348ABD")
        ax2.bar([i + width for i in x], t12_vals, width, label="Context(T12)", color="#55A868")
        ax2.set_title("不同规模下各阶段平均延迟对比")
        ax2.set_xlabel("并发规模")
        ax2.set_ylabel("平均延迟 (秒)")
        ax2.set_xticks(list(x))
        ax2.set_xticklabels(scale_labels)
        ax2.legend()
        fig2.savefig(os.path.join(RESULT_DIR, "chart_scale_latency.png"), dpi=160)
        plt.close(fig2)

        # 图3: 平均总延迟 vs 规模 (折线)
        fig3, ax3 = plt.subplots(figsize=(8, 5), constrained_layout=True)
        ax3.plot(scale_labels, avg_vals, marker="o", linewidth=2, color="#4C72B0", label="平均总延迟")
        ax3.set_title("不同规模下全流程平均延迟趋势")
        ax3.set_xlabel("并发规模")
        ax3.set_ylabel("平均总延迟 (秒)")
        ax3.legend()
        for i, val in enumerate(avg_vals):
            ax3.annotate(f"{val:.2f}s", (scale_labels[i], val),
                         textcoords="offset points", xytext=(0, 10), ha="center")
        fig3.savefig(os.path.join(RESULT_DIR, "chart_scale_trend.png"), dpi=160)
        plt.close(fig3)

        # 图4: MCP 越权拦截通过率 vs 规模
        fig4, ax4 = plt.subplots(figsize=(8, 5), constrained_layout=True)
        bars = ax4.bar(scale_labels, mcp_abuse_pass_rate_vals, color="#55A868", width=0.5)
        ax4.set_title("不同规模下 MCP 越权拦截通过率")
        ax4.set_xlabel("并发规模")
        ax4.set_ylabel("通过率 (%)")
        ax4.set_ylim(0, 100)
        for bar, val in zip(bars, mcp_abuse_pass_rate_vals):
            ax4.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                     f"{val:.1f}%", ha="center", va="bottom", fontsize=10)
        fig4.savefig(os.path.join(RESULT_DIR, "chart_scale_mcp_abuse_passrate.png"), dpi=160)
        plt.close(fig4)

        # 图5: MCP 越权请求平均延迟 vs 规模
        fig5, ax5 = plt.subplots(figsize=(8, 5), constrained_layout=True)
        ax5.plot(scale_labels, mcp_abuse_latency_vals, marker="o", linewidth=2, color="#C44E52")
        ax5.set_title("不同规模下 MCP 越权请求平均延迟")
        ax5.set_xlabel("并发规模")
        ax5.set_ylabel("平均延迟 (秒)")
        for i, val in enumerate(mcp_abuse_latency_vals):
            ax5.annotate(f"{val:.3f}s", (scale_labels[i], val),
                         textcoords="offset points", xytext=(0, 10), ha="center")
        fig5.savefig(os.path.join(RESULT_DIR, "chart_scale_mcp_abuse_latency.png"), dpi=160)
        plt.close(fig5)

        print(f"  [OK] 图表已保存到 {RESULT_DIR}/chart_scale_*.png")
    except ImportError:
        print("  [WARN] matplotlib 不可用，跳过图表生成")
    except Exception as e:
        print(f"  [ERR] 图表生成失败: {e}")


def main():
    print("=" * 60)
    print("  多规模并发测试自动化")
    print(f"  目标规模: {SCALE_TARGETS}")
    print("=" * 60)

    ensure_result_dir()

    # 检查密钥文件
    for path in [VERIFIERS_KEY_PATH, HOLDERS_KEY_PATH]:
        if not os.path.exists(path):
            print(f"[ERR] 密钥文件不存在: {path}")
            print("请先运行 setup_agents_N.py 生成足够数量的账户")
            sys.exit(1)

    # 检查账户数量
    v_config = load_key_config(VERIFIERS_KEY_PATH)
    h_config = load_key_config(HOLDERS_KEY_PATH)
    v_roles = get_sorted_roles(v_config.get("accounts", {}), "verifier")
    h_roles = get_sorted_roles(h_config.get("accounts", {}), "holder")
    max_possible = min(len(v_roles), len(h_roles))
    print(f"  可用 Verifier 账户: {len(v_roles)}")
    print(f"  可用 Holder 账户: {len(h_roles)}")
    print(f"  最大可测规模: {max_possible}v{max_possible}")

    # 过滤超出能力范围的规模
    actual_targets = [s for s in SCALE_TARGETS if s <= max_possible]
    if not actual_targets:
        print("[ERR] 无可运行的规模目标")
        sys.exit(1)

    # 逐规模运行
    all_metrics = []
    for scale in actual_targets:
        try:
            metric = run_scale_test(scale)
            all_metrics.append(metric)
        except Exception as e:
            print(f"  [ERR] 规模 {scale} 测试异常: {e}")
            all_metrics.append({"scale": scale, "count": 0, "avg_t4": 0,
                                "avg_t8": 0, "avg_t12": 0, "avg_total": 0,
                                "max_total": 0, "tps": 0,
                                "mcp_abuse_total": 0, "mcp_abuse_passed": 0,
                                "mcp_abuse_pass_rate": 0, "mcp_abuse_avg_latency": 0,
                                "mcp_abuse_max_latency": 0})
        # 规模间休息
        time.sleep(3)

    # 写入对比 CSV
    fieldnames = [
        "scale", "count", "avg_t4", "avg_t8", "avg_t12", "avg_total", "max_total", "tps",
        "mcp_abuse_total", "mcp_abuse_passed", "mcp_abuse_pass_rate",
        "mcp_abuse_avg_latency", "mcp_abuse_max_latency",
    ]
    try:
        with open(SCALE_CSV_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_metrics)
        print(f"\n[OK] 对比报表: {SCALE_CSV_PATH}")
    except Exception as e:
        print(f"[ERR] 保存对比报表失败: {e}")

    # 生成对比图表
    generate_comparison_chart(all_metrics)

    # 打印汇总
    print(f"\n{'='*118}")
    print(
        f"{'规模':<10} | {'成功数':<8} | {'Avg T4(s)':<12} | {'Avg T8(s)':<12} | {'Avg T12(s)':<12} | "
        f"{'Avg Total(s)':<14} | {'TPS':<8} | {'MCP拦截':<12} | {'MCP均延迟(s)':<13}"
    )
    print("-" * 118)
    for m in all_metrics:
        mcp_ratio = (
            f"{int(m.get('mcp_abuse_passed', 0))}/{int(m.get('mcp_abuse_total', 0))}"
            if int(m.get("mcp_abuse_total", 0)) > 0
            else "0/0"
        )
        print(
            f"{m['scale']}v{m['scale']:<7} | {m['count']:<8} | {m['avg_t4']:<12} | {m['avg_t8']:<12} | "
            f"{m['avg_t12']:<12} | {m['avg_total']:<14} | {m['tps']:<8} | "
            f"{mcp_ratio:<12} | {m.get('mcp_abuse_avg_latency', 0):<13}"
        )
    print("=" * 118)
    print("\n说明：")
    print("  - TPS = 成功审计数 / 最慢完成时间（批次吞吐量）")
    print("  - Auth(T4)延迟包含 DID 解析 + VP 验签")
    print("  - Probe(T8)延迟主要由 LLM 推理耗时决定")
    print("  - Context(T12)延迟为本地哈希比对，通常毫秒级")
    print("  - MCP联动压测统计非法 action/resource 的并发请求拦截率（期望 HTTP 403）")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
