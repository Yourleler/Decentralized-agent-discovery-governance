"""
MCP 互操作图表生成模块。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def generate_mcp_charts(
    run_dir: Path,
    case_assertions: list[dict[str, Any]],
    mcp_metrics: list[dict[str, Any]],
) -> list[str]:
    """生成 MCP 互操作阶段的核心图表。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        print("[MCP_VIZ] matplotlib 不可用，跳过 MCP 图表")
        return []

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.style.use("seaborn-v0_8-whitegrid")

    chart_files: list[str] = []

    batch_metrics = [m for m in mcp_metrics if str(m.get("stat_scope")) == "batch"]
    if batch_metrics:
        chart = _draw_latency_distribution(run_dir, batch_metrics)
        if chart:
            chart_files.append(chart)

    mcp_assertions = [row for row in case_assertions if str(row.get("phase")) == "mcp_interop"]
    if mcp_assertions:
        chart = _draw_test_matrix(run_dir, mcp_assertions)
        if chart:
            chart_files.append(chart)

    single_call_metrics = [
        row for row in mcp_metrics
        if "tool_call" in str(row.get("metric_name", "")) and str(row.get("stat_scope", "")) != "batch"
    ]
    if single_call_metrics:
        chart = _draw_tool_comparison(run_dir, single_call_metrics)
        if chart:
            chart_files.append(chart)

    matrix_metrics = [m for m in mcp_metrics if str(m.get("stat_scope")) == "matrix_summary"]
    if matrix_metrics:
        chart = _draw_latency_matrix(run_dir, matrix_metrics)
        if chart:
            chart_files.append(chart)

    return chart_files


def _draw_latency_distribution(run_dir: Path, batch_metrics: list[dict[str, Any]]) -> str | None:
    """绘制 MCP 批量调用延迟分布图。"""
    import matplotlib.pyplot as plt

    stat_map: dict[str, float] = {}
    server_id = "unknown"
    for row in batch_metrics:
        stat_map[str(row.get("metric_name", ""))] = float(row.get("value", 0.0) or 0.0)
        server_id = str(row.get("server_id", server_id))

    labels = ["min", "avg", "p50", "p95", "max"]
    keys = ["MCP_ToolCall_Min", "MCP_ToolCall_Avg", "MCP_ToolCall_P50", "MCP_ToolCall_P95", "MCP_ToolCall_Max"]
    values = [stat_map.get(key, 0.0) for key in keys]
    if all(value <= 0 for value in values):
        return None

    fig, ax = plt.subplots(figsize=(8.8, 4.6), constrained_layout=True)
    bars = ax.bar(labels, values, color=["#2ca02c", "#1f77b4", "#17becf", "#ff7f0e", "#d62728"])
    ax.set_title(f"MCP Batch Call Latency ({server_id})")
    ax.set_xlabel("Statistic")
    ax.set_ylabel("Seconds")

    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2.0, value + 0.001, f"{value*1000:.1f}ms", ha="center", fontsize=9)

    chart_path = str(run_dir / "chart_mcp_latency_distribution.png")
    fig.savefig(chart_path, dpi=180)
    plt.close(fig)
    print(f"[MCP_VIZ] 已生成: {chart_path}")
    return chart_path


def _draw_test_matrix(run_dir: Path, mcp_assertions: list[dict[str, Any]]) -> str | None:
    """绘制 MCP 用例分类通过率图。"""
    import matplotlib.pyplot as plt

    category_map = {
        "mcp_interop.stdio_connect": "connect",
        "mcp_interop.tools_discovery": "discover",
        "mcp_interop.tool_call": "tool_call",
        "mcp_interop.resources_graceful": "resources",
        "mcp_interop.vc_auth_positive": "auth_positive",
        "mcp_interop.vc_auth_negative": "auth_negative",
        "mcp_interop.latency_batch": "latency_batch",
        "mcp_interop.latency_matrix": "latency_matrix",
    }

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in mcp_assertions:
        capability = str(row.get("capability_id", ""))
        category = category_map.get(capability, "other")
        grouped.setdefault(category, []).append(row)

    categories = [name for name, rows in grouped.items() if rows]
    if not categories:
        return None

    totals = [len(grouped[name]) for name in categories]
    pass_rates = [sum(1 for row in grouped[name] if bool(row.get("passed"))) / max(1, len(grouped[name])) for name in categories]

    fig_h = max(4.2, 0.52 * len(categories) + 1.5)
    fig, ax = plt.subplots(figsize=(9.6, fig_h), constrained_layout=True)
    y = list(range(len(categories)))
    bars = ax.barh(y, pass_rates, color=["#2ca02c" if rate >= 1.0 else "#ff7f0e" for rate in pass_rates])

    ax.set_yticks(y)
    ax.set_yticklabels(categories)
    ax.invert_yaxis()
    ax.set_xlim(0, 1.05)
    ax.set_xlabel("Pass Rate")
    ax.set_ylabel("Category")
    ax.set_title("MCP Test Pass Matrix")

    for idx, (bar, rate, total) in enumerate(zip(bars, pass_rates, totals)):
        passed = int(round(rate * total))
        ax.text(min(rate + 0.02, 0.98), idx, f"{passed}/{total}", va="center", fontsize=9)

    chart_path = str(run_dir / "chart_mcp_test_matrix.png")
    fig.savefig(chart_path, dpi=180)
    plt.close(fig)
    print(f"[MCP_VIZ] 已生成: {chart_path}")
    return chart_path


def _draw_tool_comparison(run_dir: Path, metrics: list[dict[str, Any]]) -> str | None:
    """绘制 MCP 单次工具调用延迟对比图。"""
    import matplotlib.pyplot as plt

    tool_names: list[str] = []
    latencies: list[float] = []
    for row in metrics:
        tool_name = str(row.get("tool_name", row.get("metric_name", "unknown")))
        latency = float(row.get("value", 0.0) or 0.0)
        tool_names.append(tool_name)
        latencies.append(latency)

    if not tool_names:
        return None

    fig, ax = plt.subplots(figsize=(8.6, 4.8), constrained_layout=True)
    bars = ax.bar(tool_names, latencies, color="#1f77b4")
    ax.set_title("MCP Single Tool Call Latency")
    ax.set_xlabel("Tool")
    ax.set_ylabel("Seconds")

    for bar, latency in zip(bars, latencies):
        ax.text(bar.get_x() + bar.get_width() / 2.0, latency + 0.005, f"{latency:.3f}s", ha="center", fontsize=9)

    chart_path = str(run_dir / "chart_mcp_tool_comparison.png")
    fig.savefig(chart_path, dpi=180)
    plt.close(fig)
    print(f"[MCP_VIZ] 已生成: {chart_path}")
    return chart_path


def _draw_latency_matrix(run_dir: Path, metrics: list[dict[str, Any]]) -> str | None:
    """绘制 MCP 并发延迟矩阵图。"""
    import matplotlib.pyplot as plt

    grouped: dict[str, dict[str, float]] = {}
    for row in metrics:
        level = str(row.get("load_level", "")).strip()
        if not level:
            continue
        metric_name = str(row.get("metric_name", ""))
        grouped.setdefault(level, {})
        grouped[level][metric_name] = float(row.get("value", 0.0) or 0.0)
        grouped[level]["pass_rate"] = float(row.get("pass_rate", 0.0) or 0.0)
        grouped[level]["concurrency"] = float(row.get("concurrency", 0.0) or 0.0)

    if not grouped:
        return None

    levels = sorted(grouped.keys())
    p95_values = [grouped[level].get("MCP_Matrix_P95", 0.0) for level in levels]
    avg_values = [grouped[level].get("MCP_Matrix_Avg", 0.0) for level in levels]
    pass_rates = [grouped[level].get("pass_rate", 0.0) * 100.0 for level in levels]
    conc_values = [grouped[level].get("concurrency", 0.0) for level in levels]

    x = list(range(len(levels)))
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.8), constrained_layout=True)

    axes[0].plot(x, avg_values, marker="o", linewidth=2.0, color="#1f77b4", label="avg")
    axes[0].plot(x, p95_values, marker="s", linewidth=2.0, color="#ff7f0e", label="p95")
    axes[0].set_xticks(x)
    axes[0].set_xticklabels(levels)
    axes[0].set_xlabel("Load Level")
    axes[0].set_ylabel("Seconds")
    axes[0].set_title("MCP Latency Matrix")
    axes[0].legend()

    bars = axes[1].bar(x, pass_rates, color="#2ca02c")
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(levels)
    axes[1].set_xlabel("Load Level")
    axes[1].set_ylabel("Pass Rate (%)")
    axes[1].set_ylim(0, 105)
    axes[1].set_title("MCP Matrix Reliability")
    for idx, bar in enumerate(bars):
        y = bar.get_height()
        axes[1].text(
            idx,
            y + 1.2,
            f"{y:.1f}%\nC={int(conc_values[idx])}",
            ha="center",
            fontsize=8,
        )

    chart_path = str(run_dir / "chart_mcp_latency_matrix.png")
    fig.savefig(chart_path, dpi=180)
    plt.close(fig)
    print(f"[MCP_VIZ] 已生成: {chart_path}")
    return chart_path
