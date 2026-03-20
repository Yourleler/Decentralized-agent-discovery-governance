
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt


def _setup_matplotlib() -> None:
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.style.use("ggplot")


def _save(fig: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _sort_labels_values(labels: list[str], values: list[float]) -> tuple[list[str], list[float]]:
    pairs = sorted(zip(labels, values), key=lambda item: item[1], reverse=True)
    if not pairs:
        return [], []
    return [item[0] for item in pairs], [item[1] for item in pairs]


def _shorten(text: str, max_len: int = 42) -> str:
    raw = str(text)
    if len(raw) <= max_len:
        return raw
    return f"{raw[: max_len - 1]}…"


def build_latency_stage_chart(run_dir: Path, latency_stats_rows: list[dict[str, Any]]) -> str | None:
    stage_rows = [row for row in latency_stats_rows if str(row.get("stat_scope")) == "stage"]
    if not stage_rows:
        return None

    order = ["T4_auth", "T8_probe", "T12_context", "Total"]
    order_map = {name: idx for idx, name in enumerate(order)}
    stage_rows = sorted(stage_rows, key=lambda row: order_map.get(str(row.get("latency_stage")), 99))

    label_map = {
        "T4_auth": "认证T4",
        "T8_probe": "探测T8",
        "T12_context": "上下文T12",
        "Total": "全流程",
    }
    labels = [label_map.get(str(row.get("latency_stage")), str(row.get("latency_stage"))) for row in stage_rows]
    mean_values = [_float(row.get("mean_seconds")) for row in stage_rows]
    p95_values = [_float(row.get("p95_seconds")) for row in stage_rows]

    x = list(range(len(labels)))
    width = 0.36
    fig, ax = plt.subplots(figsize=(9.2, 5.0), constrained_layout=True)
    ax.bar([i - width / 2 for i in x], mean_values, width=width, label="均值(s)", color="#E24A33")
    ax.bar([i + width / 2 for i in x], p95_values, width=width, label="P95(s)", color="#348ABD")
    ax.set_title("各阶段延迟统计（均值与P95）")
    ax.set_xlabel("阶段")
    ax.set_ylabel("秒")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.legend()

    filename = "chart_latency_stage.png"
    _save(fig, run_dir / filename)
    return filename


def build_case_passrate_chart(run_dir: Path, case_assertions: list[dict[str, Any]]) -> str | None:
    if not case_assertions:
        return None

    grouped: dict[str, list[bool]] = defaultdict(list)
    for row in case_assertions:
        capability_id = str(row.get("capability_id", "")).strip() or "未标注能力"
        grouped[capability_id].append(bool(row.get("passed")))

    labels = list(grouped.keys())
    rates = [
        (sum(1 for item in grouped[label] if item) / len(grouped[label]) * 100.0) if grouped[label] else 0.0
        for label in labels
    ]
    labels = [_shorten(item, 56) for item in labels]
    labels, rates = _sort_labels_values(labels, rates)
    if not labels:
        return None

    fig_h = max(4.6, 0.42 * len(labels) + 1.8)
    fig, ax = plt.subplots(figsize=(11.2, fig_h), constrained_layout=True)
    y = list(range(len(labels)))
    bars = ax.barh(y, rates, color="#4C72B0")
    ax.set_title("能力维度用例通过率")
    ax.set_xlabel("通过率(%)")
    ax.set_ylabel("能力ID")
    ax.set_xlim(0, 105)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()

    for bar, value in zip(bars, rates):
        y_pos = bar.get_y() + bar.get_height() / 2.0
        ax.text(min(value + 1.0, 103.0), y_pos, f"{value:.1f}%", va="center", fontsize=9)

    filename = "chart_case_passrate.png"
    _save(fig, run_dir / filename)
    return filename


def build_tx_cost_chart(run_dir: Path, chain_tx_rows: list[dict[str, Any]]) -> str | None:
    if not chain_tx_rows:
        return None

    by_type: dict[str, float] = defaultdict(float)
    for row in chain_tx_rows:
        tx_type = str(row.get("tx_type", row.get("category", "unknown")))
        by_type[tx_type] += _float(row.get("cost_eth"))

    labels = list(by_type.keys())
    costs = [by_type[label] for label in labels]
    labels = [_shorten(item, 48) for item in labels]
    labels, costs = _sort_labels_values(labels, costs)
    if not costs:
        return None

    fig_h = max(4.2, 0.45 * len(labels) + 1.8)
    fig, ax = plt.subplots(figsize=(10.8, fig_h), constrained_layout=True)
    y = list(range(len(labels)))
    bars = ax.barh(y, costs, color="#55A868")
    ax.set_title("按交易类别聚合的链上成本（ETH）")
    ax.set_xlabel("成本(ETH)")
    ax.set_ylabel("交易类别")
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()

    for bar, value in zip(bars, costs):
        y_pos = bar.get_y() + bar.get_height() / 2.0
        ax.text(value, y_pos, f" {value:.2e}", va="center", fontsize=9)

    filename = "chart_tx_cost_eth.png"
    _save(fig, run_dir / filename)
    return filename

def build_l2_cost_chart(run_dir: Path, l2_summary_rows: list[dict[str, Any]]) -> str | None:
    if not l2_summary_rows:
        return None

    labels = [str(row.get("l2_name", "")) for row in l2_summary_rows]
    avg_costs = [_float(row.get("avg_cost_cny_per_tx")) for row in l2_summary_rows]
    total_costs = [_float(row.get("cost_cny")) for row in l2_summary_rows]
    if not labels:
        return None

    x = list(range(len(labels)))
    width = 0.35
    fig, ax = plt.subplots(figsize=(9.2, 5.0), constrained_layout=True)
    ax.bar([i - width / 2 for i in x], avg_costs, width=width, label="单笔均值(CNY)", color="#E24A33")
    ax.bar([i + width / 2 for i in x], total_costs, width=width, label="总成本(CNY)", color="#348ABD")
    ax.set_title("L2 成本人民币估算对比")
    ax.set_xlabel("L2 网络")
    ax.set_ylabel("人民币(CNY)")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.legend()

    filename = "chart_l2_cost_cny.png"
    _save(fig, run_dir / filename)
    return filename


def build_scale_projection_chart(run_dir: Path, scale_projection_rows: list[dict[str, Any]]) -> str | None:
    if not scale_projection_rows:
        return None

    rows = sorted(scale_projection_rows, key=lambda row: int(_float(row.get("target_agents"), 0)))
    labels = [str(int(_float(row.get("target_agents"), 0))) for row in rows]
    time_values = [_float(row.get("est_total_time_seconds"), 0.0) for row in rows]
    cost_values = [_float(row.get("est_total_cost_cny"), 0.0) for row in rows]

    if not labels:
        return None

    x = list(range(len(labels)))
    fig, ax1 = plt.subplots(figsize=(9.4, 5.0), constrained_layout=True)
    ax2 = ax1.twinx()

    ax1.plot(x, time_values, marker="o", color="#4C72B0", linewidth=2.0, label="总时延估算(s)")
    ax2.plot(x, cost_values, marker="s", color="#55A868", linewidth=2.0, label="总成本估算(CNY)")

    ax1.set_title("大规模运行估算（线性外推）")
    ax1.set_xlabel("目标 Agent 数")
    ax1.set_ylabel("总时延估算(秒)")
    ax2.set_ylabel("总成本估算(CNY)")
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels)

    lines_1, labels_1 = ax1.get_legend_handles_labels()
    lines_2, labels_2 = ax2.get_legend_handles_labels()
    ax1.legend(lines_1 + lines_2, labels_1 + labels_2, loc="upper left")

    filename = "chart_scale_projection.png"
    _save(fig, run_dir / filename)
    return filename


def generate_charts(
    run_dir: Path,
    latency_stats_rows: list[dict[str, Any]],
    case_assertions: list[dict[str, Any]],
    chain_tx_rows: list[dict[str, Any]],
    l2_summary_rows: list[dict[str, Any]],
    scale_projection_rows: list[dict[str, Any]],
) -> dict[str, str]:
    _setup_matplotlib()
    chart_files: dict[str, str] = {}

    builders = [
        lambda: build_latency_stage_chart(run_dir=run_dir, latency_stats_rows=latency_stats_rows),
        lambda: build_case_passrate_chart(run_dir=run_dir, case_assertions=case_assertions),
        lambda: build_tx_cost_chart(run_dir=run_dir, chain_tx_rows=chain_tx_rows),
        lambda: build_l2_cost_chart(run_dir=run_dir, l2_summary_rows=l2_summary_rows),
        lambda: build_scale_projection_chart(run_dir=run_dir, scale_projection_rows=scale_projection_rows),
    ]

    for builder in builders:
        try:
            name = builder()
        except Exception:
            name = None
        if name:
            chart_files[name] = name

    return chart_files
