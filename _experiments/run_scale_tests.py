"""
多规模并发测试自动化脚本。

用途：按顺序运行多个规模（如 1v1, 3v3, 5v5, 10v10）的 P2P 认证压测，
汇总为对比 CSV + 规模分析图表，用于论文中"不同规模下的响应延迟与互操作性"章节。

前置条件：
  1. 已通过 setup_agents_N.py 生成目标规模的账户（verifiers_key.json / holders_key.json）
  2. Issuer 服务已启动（_ops_services/issuer_server.py）

运行方式：
  python _experiments/run_scale_tests.py

说明：
  - 每个规模会先启动对应数量的 Holder，再启动对应数量的 Verifier 并发测试
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
from pathlib import Path

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

# === 测试规模配置 ===
SCALE_TARGETS = [1, 3, 5, 10]  # 并发对数

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
            print(f"  ⚠️ 超时！已收集 {collected}/{num_pairs}")
            break
        try:
            data = stats_queue.get(timeout=10)
            results.append(data)
            collected += 1
            print(f"  已完成: {collected}/{num_pairs}")
        except Exception:
            pass

    # 5. 清理
    for p in verifier_procs:
        if p.is_alive():
            p.terminate()
        p.join(timeout=5)
    stop_processes(holder_procs)

    # 6. 计算统计指标
    if not results:
        return {
            "scale": num_pairs,
            "count": 0,
            "avg_t4": 0, "avg_t8": 0, "avg_t12": 0, "avg_total": 0,
            "max_total": 0, "tps": 0,
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
        print(f"  ✅ 原始数据: {scale_csv}")
    except Exception as e:
        print(f"  ❌ 保存失败: {e}")

    metric = {
        "scale": num_pairs,
        "count": count,
        "avg_t4": round(avg_t4, 4),
        "avg_t8": round(avg_t8, 4),
        "avg_t12": round(avg_t12, 4),
        "avg_total": round(avg_total, 4),
        "max_total": round(max_total, 4),
        "tps": round(tps, 4),
    }
    print(f"  📊 规模={num_pairs}, TPS={tps:.4f}, Avg={avg_total:.4f}s, Max={max_total:.4f}s")
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

        if not scales:
            print("  ⚠️ 无有效数据，跳过绘图")
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

        print(f"  ✅ 图表已保存到 {RESULT_DIR}/chart_scale_*.png")
    except ImportError:
        print("  ⚠️ matplotlib 不可用，跳过图表生成")
    except Exception as e:
        print(f"  ❌ 图表生成失败: {e}")


def main():
    print("=" * 60)
    print("  多规模并发测试自动化")
    print(f"  目标规模: {SCALE_TARGETS}")
    print("=" * 60)

    ensure_result_dir()

    # 检查密钥文件
    for path in [VERIFIERS_KEY_PATH, HOLDERS_KEY_PATH]:
        if not os.path.exists(path):
            print(f"❌ 密钥文件不存在: {path}")
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
        print("❌ 无可运行的规模目标")
        sys.exit(1)

    # 逐规模运行
    all_metrics = []
    for scale in actual_targets:
        try:
            metric = run_scale_test(scale)
            all_metrics.append(metric)
        except Exception as e:
            print(f"  ❌ 规模 {scale} 测试异常: {e}")
            all_metrics.append({"scale": scale, "count": 0, "avg_t4": 0,
                                "avg_t8": 0, "avg_t12": 0, "avg_total": 0,
                                "max_total": 0, "tps": 0})
        # 规模间休息
        time.sleep(3)

    # 写入对比 CSV
    fieldnames = ["scale", "count", "avg_t4", "avg_t8", "avg_t12", "avg_total", "max_total", "tps"]
    try:
        with open(SCALE_CSV_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_metrics)
        print(f"\n✅ 对比报表: {SCALE_CSV_PATH}")
    except Exception as e:
        print(f"❌ 保存对比报表失败: {e}")

    # 生成对比图表
    generate_comparison_chart(all_metrics)

    # 打印汇总
    print(f"\n{'='*80}")
    print(f"{'规模':<10} | {'成功数':<8} | {'Avg T4(s)':<12} | {'Avg T8(s)':<12} | {'Avg T12(s)':<12} | {'Avg Total(s)':<14} | {'TPS':<8}")
    print("-" * 80)
    for m in all_metrics:
        print(f"{m['scale']}v{m['scale']:<7} | {m['count']:<8} | {m['avg_t4']:<12} | {m['avg_t8']:<12} | {m['avg_t12']:<12} | {m['avg_total']:<14} | {m['tps']:<8}")
    print("=" * 80)
    print("\n说明：")
    print("  - TPS = 成功审计数 / 最慢完成时间（批次吞吐量）")
    print("  - Auth(T4)延迟包含 DID 解析 + VP 验签")
    print("  - Probe(T8)延迟主要由 LLM 推理耗时决定")
    print("  - Context(T12)延迟为本地哈希比对，通常毫秒级")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
