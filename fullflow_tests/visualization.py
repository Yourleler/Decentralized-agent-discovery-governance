from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np


def _setup_matplotlib() -> None:
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.style.use("seaborn-v0_8-whitegrid")


def _save(fig: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _shorten(text: str, max_len: int = 40) -> str:
    raw = str(text)
    return raw if len(raw) <= max_len else f"{raw[: max_len - 1]}…"


def _annotate_bar(ax: Any, bar: Any, label: str, offset_frac: float = 0.02) -> None:
    """在柱顶标注文字，offset_frac 为 y 轴范围的比例偏移。"""
    ylim = ax.get_ylim()
    offset = (ylim[1] - ylim[0]) * offset_frac
    ax.text(
        bar.get_x() + bar.get_width() / 2.0,
        bar.get_height() + offset,
        label,
        ha="center",
        va="bottom",
        fontsize=8,
    )


def build_latency_stage_chart(run_dir: Path, latency_stats_rows: list[dict[str, Any]]) -> str | None:
    """绘制主链路阶段延迟（均值/P95）对比图。"""
    stage_rows = [row for row in latency_stats_rows if str(row.get("stat_scope")) == "stage"]
    if not stage_rows:
        return None

    order = ["T4_auth", "T8_probe", "T12_context", "Total"]
    name_map = {"T4_auth": "Auth(T4)", "T8_probe": "Probe(T8)", "T12_context": "Context(T12)", "Total": "End-to-End"}
    idx_map = {name: i for i, name in enumerate(order)}
    stage_rows = sorted(stage_rows, key=lambda r: idx_map.get(str(r.get("latency_stage")), 999))

    labels = [name_map.get(str(r.get("latency_stage")), str(r.get("latency_stage"))) for r in stage_rows]
    mean_vals = [_float(r.get("mean_seconds")) for r in stage_rows]
    p95_vals = [_float(r.get("p95_seconds")) for r in stage_rows]
    counts = [int(_float(r.get("count"))) for r in stage_rows]

    x = np.arange(len(labels))
    width = 0.35
    fig, ax = plt.subplots(figsize=(10, 5), constrained_layout=True)
    bars_mean = ax.bar(x - width / 2, mean_vals, width, color="#1f77b4", label="Mean")
    bars_p95 = ax.bar(x + width / 2, p95_vals, width, color="#ff7f0e", label="P95")
    ax.set_title("Verification Stage Latency")
    ax.set_xlabel("Stage")
    ax.set_ylabel("Seconds")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.legend()
    for bar, val, cnt in zip(bars_mean, mean_vals, counts):
        _annotate_bar(ax, bar, f"{val:.2f}s\nn={cnt}")
    for bar, val in zip(bars_p95, p95_vals):
        _annotate_bar(ax, bar, f"{val:.2f}s")

    filename = "chart_latency_stage.png"
    _save(fig, run_dir / filename)
    return filename


def build_security_negative_matrix_chart(run_dir: Path, case_assertions: list[dict[str, Any]]) -> str | None:
    """绘制安全负例通过/失败矩阵图。"""
    verification_rows = [
        r for r in case_assertions
        if str(r.get("phase")) == "verification" and "negative" in str(r.get("case_id", ""))
    ]
    if not verification_rows:
        return None

    grouped: dict[str, dict[str, int]] = {}
    for r in verification_rows:
        key = str(r.get("case_id", "")).replace("verification_negative_", "") or str(r.get("case_id", ""))
        bucket = grouped.setdefault(key, {"passed": 0, "failed": 0})
        if bool(r.get("passed")):
            bucket["passed"] += 1
        else:
            bucket["failed"] += 1

    labels = [_shorten(k, 46) for k in grouped]
    passed_vals = [grouped[k]["passed"] for k in grouped]
    failed_vals = [grouped[k]["failed"] for k in grouped]
    if not labels:
        return None

    fig_h = max(4.5, 0.5 * len(labels) + 2.0)
    fig, ax = plt.subplots(figsize=(12, fig_h), constrained_layout=True)
    y = list(range(len(labels)))
    ax.barh(y, passed_vals, color="#2ca02c", label="Passed")
    ax.barh(y, failed_vals, left=passed_vals, color="#d62728", label="Failed")
    for i, (p, f) in enumerate(zip(passed_vals, failed_vals)):
        total = p + f
        ax.text(total + 0.05, i, f"{p}/{total}", va="center", fontsize=9)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel("Case Count")
    ax.set_title("Verification Security Negative Matrix")
    ax.legend(loc="lower right")

    filename = "chart_security_negative_matrix.png"
    _save(fig, run_dir / filename)
    return filename


def build_concurrency_stress_chart(run_dir: Path, phase_metrics: list[dict[str, Any]]) -> str | None:
    """绘制并发压测矩阵图（各档位通过率/P95/吞吐）。"""
    summaries = sorted(
        [r for r in phase_metrics if str(r.get("scenario")) == "concurrency_stress_summary"],
        key=lambda r: str(r.get("load_level", r.get("case_id", ""))),
    )
    if not summaries:
        return None

    labels = [str(r.get("load_level", "L1")) for r in summaries]
    pass_rates = [_float(r.get("pass_rate")) * 100.0 for r in summaries]
    p95_vals = [_float(r.get("p95_duration_seconds")) for r in summaries]
    tps_vals = [_float(r.get("throughput_tps")) for r in summaries]
    task_vals = [int(_float(r.get("total_tasks"))) for r in summaries]
    x = np.arange(len(labels))

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5), constrained_layout=True)

    bars = axes[0].bar(x, pass_rates, color="#2ca02c")
    axes[0].set_title("Concurrency Matrix — Pass Rate")
    axes[0].set_ylabel("Pass Rate (%)")
    axes[0].set_xticks(x)
    axes[0].set_xticklabels(labels)
    axes[0].set_ylim(0, 115)
    for bar, rate, n in zip(bars, pass_rates, task_vals):
        _annotate_bar(axes[0], bar, f"{rate:.1f}%\nN={n}")

    ax_p95 = axes[1]
    ax_tps = ax_p95.twinx()
    ax_p95.plot(x, p95_vals, marker="o", linewidth=2, color="#ff7f0e", label="P95 (s)")
    ax_tps.plot(x, tps_vals, marker="s", linewidth=2, color="#1f77b4", label="TPS")
    axes[1].set_title("Concurrency Matrix — P95 & Throughput")
    ax_p95.set_xticks(x)
    ax_p95.set_xticklabels(labels)
    ax_p95.set_ylabel("P95 Latency (s)")
    ax_tps.set_ylabel("Throughput (tps)")

    # 标注：偏移量基于各自 y 轴范围
    p95_range = max(p95_vals) - min(p95_vals) if len(p95_vals) > 1 else max(p95_vals, default=1)
    tps_range = max(tps_vals) - min(tps_vals) if len(tps_vals) > 1 else max(tps_vals, default=1)
    p95_off = (p95_range or 1) * 0.04
    tps_off = (tps_range or 1) * 0.04
    for xi, (pv, tv) in enumerate(zip(p95_vals, tps_vals)):
        ax_p95.annotate(f"{pv:.3f}", (xi, pv), xytext=(0, 6), textcoords="offset points", ha="center", fontsize=8)
        ax_tps.annotate(f"{tv:.2f}", (xi, tv), xytext=(0, -14), textcoords="offset points", ha="center", fontsize=8)

    lines1, labs1 = ax_p95.get_legend_handles_labels()
    lines2, labs2 = ax_tps.get_legend_handles_labels()
    ax_p95.legend(lines1 + lines2, labs1 + labs2, loc="upper left", fontsize=8)

    filename = "chart_concurrency_stress.png"
    _save(fig, run_dir / filename)
    return filename


def build_mcp_abuse_matrix_chart(run_dir: Path, phase_metrics: list[dict[str, Any]]) -> str | None:
    """绘制 MCP 越权并发负例矩阵图。"""
    rows = sorted(
        [r for r in phase_metrics if str(r.get("scenario")) == "negative" and str(r.get("negative_case")) == "mcp_abuse_concurrency"],
        key=lambda r: str(r.get("load_level", r.get("case_id", ""))),
    )
    if not rows:
        return None

    labels = [str(r.get("load_level", "L1")) for r in rows]
    pass_rates = [_float(r.get("pass_rate")) * 100.0 for r in rows]
    req_counts = [int(_float(r.get("total_requests"))) for r in rows]
    max_lats = [_float(r.get("max_latency_seconds")) for r in rows]
    x = np.arange(len(labels))

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5), constrained_layout=True)

    bars = axes[0].bar(x, pass_rates, color="#2ca02c")
    axes[0].set_title("MCP Abuse Matrix — Block Rate")
    axes[0].set_ylabel("Blocked Rate (%)")
    axes[0].set_xticks(x)
    axes[0].set_xticklabels(labels)
    axes[0].set_ylim(0, 115)
    for bar, rate, n in zip(bars, pass_rates, req_counts):
        _annotate_bar(axes[0], bar, f"{rate:.1f}%\nN={n}")

    axes[1].plot(x, max_lats, marker="o", linewidth=2, color="#d62728")
    axes[1].set_title("MCP Abuse Matrix — Worst-case Latency")
    axes[1].set_ylabel("Max Latency (s)")
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(labels)
    for xi, val in enumerate(max_lats):
        axes[1].annotate(f"{val:.3f}", (xi, val), xytext=(0, 6), textcoords="offset points", ha="center", fontsize=8)

    filename = "chart_mcp_abuse_matrix.png"
    _save(fig, run_dir / filename)
    return filename


def build_tx_cost_chart(run_dir: Path, chain_tx_rows: list[dict[str, Any]]) -> str | None:
    """绘制链上成本分解图（按交易类型聚合 ETH）。"""
    if not chain_tx_rows:
        return None

    grouped: dict[str, float] = defaultdict(float)
    for r in chain_tx_rows:
        tx_type = str(r.get("tx_type", r.get("category", "unknown"))).strip() or "unknown"
        grouped[tx_type] += _float(r.get("cost_eth"))

    pairs = sorted(grouped.items(), key=lambda kv: kv[1], reverse=True)
    if not pairs:
        return None
    labels = [_shorten(k, 44) for k, _ in pairs]
    values = [v for _, v in pairs]

    fig_h = max(4.5, 0.5 * len(labels) + 2.0)
    fig, ax = plt.subplots(figsize=(11, fig_h), constrained_layout=True)
    y = np.arange(len(labels))
    bars = ax.barh(y, values, color="#17becf")
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel("Cost (ETH)")
    ax.set_title("On-chain Cost Breakdown")
    ax.xaxis.set_major_formatter(mticker.FormatStrFormatter("%.2e"))
    for bar, val in zip(bars, values):
        ax.text(val, bar.get_y() + bar.get_height() / 2.0, f"  {val:.2e}", va="center", fontsize=9)

    filename = "chart_tx_cost_eth.png"
    _save(fig, run_dir / filename)
    return filename


def build_l2_cost_chart(run_dir: Path, l2_summary_rows: list[dict[str, Any]]) -> str | None:
    """绘制 L2 成本对比图（均值/总计 CNY）。"""
    if not l2_summary_rows:
        return None

    labels = [str(r.get("l2_name", "")) for r in l2_summary_rows]
    avg_costs = [_float(r.get("avg_cost_cny_per_tx")) for r in l2_summary_rows]
    total_costs = [_float(r.get("cost_cny")) for r in l2_summary_rows]
    if not labels:
        return None

    x = np.arange(len(labels))
    width = 0.35
    fig, ax = plt.subplots(figsize=(10, 5), constrained_layout=True)
    bars_avg = ax.bar(x - width / 2, avg_costs, width, label="Avg per Tx (CNY)", color="#1f77b4")
    bars_tot = ax.bar(x + width / 2, total_costs, width, label="Total (CNY)", color="#ff7f0e")
    ax.set_title("L2 Cost Estimation")
    ax.set_xlabel("L2 Network")
    ax.set_ylabel("CNY")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.legend()
    for bar, val in zip(bars_avg, avg_costs):
        _annotate_bar(ax, bar, f"{val:.4f}")
    for bar, val in zip(bars_tot, total_costs):
        _annotate_bar(ax, bar, f"{val:.4f}")

    filename = "chart_l2_cost_cny.png"
    _save(fig, run_dir / filename)
    return filename


def generate_charts(
    run_dir: Path,
    phase_metrics: list[dict[str, Any]],
    latency_stats_rows: list[dict[str, Any]],
    case_assertions: list[dict[str, Any]],
    chain_tx_rows: list[dict[str, Any]],
    l2_summary_rows: list[dict[str, Any]],
    scale_projection_rows: list[dict[str, Any]],
) -> dict[str, str]:
    """统一生成 fullflow 核心图表。scale_projection_rows 保留参数签名但不绘图（线性外推，不适合可视化）。"""
    _setup_matplotlib()
    chart_files: dict[str, str] = {}

    builders = [
        lambda: build_latency_stage_chart(run_dir=run_dir, latency_stats_rows=latency_stats_rows),
        lambda: build_security_negative_matrix_chart(run_dir=run_dir, case_assertions=case_assertions),
        lambda: build_concurrency_stress_chart(run_dir=run_dir, phase_metrics=phase_metrics),
        lambda: build_mcp_abuse_matrix_chart(run_dir=run_dir, phase_metrics=phase_metrics),
        lambda: build_tx_cost_chart(run_dir=run_dir, chain_tx_rows=chain_tx_rows),
        lambda: build_l2_cost_chart(run_dir=run_dir, l2_summary_rows=l2_summary_rows),
    ]

    for builder in builders:
        try:
            filename = builder()
        except Exception:
            filename = None
        if filename:
            chart_files[filename] = filename

    return chart_files
