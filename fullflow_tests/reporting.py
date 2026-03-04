from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    """
    功能：
    将字典列表按指定字段写入 CSV 文件。

    参数：
    path (Path): 输出 CSV 路径。
    rows (list[dict[str, Any]]): 数据行列表。
    fieldnames (list[str]): 输出字段顺序。

    返回值：
    None: 写入完成后无返回值。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            output_row = {name: row.get(name, "") for name in fieldnames}
            writer.writerow(output_row)


def mean(values: list[float]) -> float:
    """
    功能：
    计算浮点数列表的平均值。

    参数：
    values (list[float]): 待计算数值列表。

    返回值：
    float: 平均值；空列表返回 0.0。
    """
    if not values:
        return 0.0
    return float(sum(values)) / float(len(values))


def build_summary_markdown(
    phase_metrics: list[dict[str, Any]],
    chain_tx_metrics: list[dict[str, Any]],
    discovery_metrics: list[dict[str, Any]],
    governance_metrics: list[dict[str, Any]],
    usd_per_eth: float,
) -> str:
    """
    功能：
    生成 Markdown 摘要文本，展示核心性能、成本与通过率指标。

    参数：
    phase_metrics (list[dict[str, Any]]): 验证阶段指标列表。
    chain_tx_metrics (list[dict[str, Any]]): 链上交易指标列表。
    discovery_metrics (list[dict[str, Any]]): 发现阶段指标列表。
    governance_metrics (list[dict[str, Any]]): 治理阶段指标列表。
    usd_per_eth (float): ETH 对 USD 换算比例。

    返回值：
    str: Markdown 摘要文本。
    """
    positive_rows = [
        row
        for row in phase_metrics
        if row.get("scenario") == "positive"
    ]
    passed_rows = [row for row in positive_rows if row.get("status") == "passed"]

    auth_lat = [float(row.get("T4", 0.0) or 0.0) for row in passed_rows]
    probe_lat = [float(row.get("T8", 0.0) or 0.0) for row in passed_rows]
    ctx_lat = [float(row.get("T12", 0.0) or 0.0) for row in passed_rows]
    total_lat = [float(row.get("Total_Duration", 0.0) or 0.0) for row in passed_rows]

    round_rows = [row for row in phase_metrics if row.get("scenario") == "round_summary"]
    tps_values = [float(row.get("round_tps", 0.0) or 0.0) for row in round_rows]

    total_cost_eth = sum(float(row.get("cost_eth", 0.0) or 0.0) for row in chain_tx_metrics)
    total_cost_usd = total_cost_eth * float(usd_per_eth)

    discovery_assertions = [
        row for row in discovery_metrics if row.get("metric_type") == "search_assertion"
    ]
    discovery_hit_count = sum(1 for row in discovery_assertions if bool(row.get("found")))

    governance_passed = sum(1 for row in governance_metrics if row.get("status") == "passed")

    lines = [
        "# Fullflow 测试摘要",
        "",
        "## 1. 验证阶段",
        f"- 正向审计总数: {len(positive_rows)}",
        f"- 正向审计通过数: {len(passed_rows)}",
        f"- Auth 平均延迟(T4): {mean(auth_lat):.4f}s",
        f"- Probe 平均延迟(T8): {mean(probe_lat):.4f}s",
        f"- Context 平均延迟(T12): {mean(ctx_lat):.4f}s",
        f"- 全流程平均延迟(Total): {mean(total_lat):.4f}s",
        f"- 平均 TPS(按轮): {mean(tps_values):.4f}",
        "",
        "## 2. 成本阶段",
        f"- 链上交易数: {len(chain_tx_metrics)}",
        f"- 总成本(ETH): {total_cost_eth:.8f}",
        f"- 估算总成本(USD): {total_cost_usd:.4f}",
        "",
        "## 3. 发现阶段",
        f"- 检索断言数量: {len(discovery_assertions)}",
        f"- 命中本次账户数量: {discovery_hit_count}",
        "",
        "## 4. 治理阶段",
        f"- 治理动作数量: {len(governance_metrics)}",
        f"- 治理通过数量: {governance_passed}",
        "",
        "## 5. 说明",
        "- 本摘要聚合自 phase_metrics.csv / chain_tx_metrics.csv / discovery_metrics.csv / governance_metrics.csv。",
    ]
    return "\n".join(lines)


def write_reports(
    run_dir: Path,
    phase_metrics: list[dict[str, Any]],
    chain_tx_metrics: list[dict[str, Any]],
    discovery_metrics: list[dict[str, Any]],
    governance_metrics: list[dict[str, Any]],
    raw_metrics: dict[str, Any],
    usd_per_eth: float,
) -> dict[str, str]:
    """
    功能：
    生成 fullflow 约定的全部报表文件并返回路径映射。

    参数：
    run_dir (Path): 本次运行目录。
    phase_metrics (list[dict[str, Any]]): 验证阶段指标。
    chain_tx_metrics (list[dict[str, Any]]): 链上交易指标。
    discovery_metrics (list[dict[str, Any]]): 发现阶段指标。
    governance_metrics (list[dict[str, Any]]): 治理阶段指标。
    raw_metrics (dict[str, Any]): 原始聚合结果对象。
    usd_per_eth (float): ETH 对 USD 换算比例。

    返回值：
    dict[str, str]: 产物文件路径映射。
    """
    run_dir.mkdir(parents=True, exist_ok=True)

    phase_csv = run_dir / "phase_metrics.csv"
    chain_csv = run_dir / "chain_tx_metrics.csv"
    discovery_csv = run_dir / "discovery_metrics.csv"
    governance_csv = run_dir / "governance_metrics.csv"
    raw_json = run_dir / "raw_metrics.json"
    summary_md = run_dir / "summary.md"

    phase_fields = sorted({k for row in phase_metrics for k in row.keys()}) if phase_metrics else ["scenario", "status"]
    chain_fields = sorted({k for row in chain_tx_metrics for k in row.keys()}) if chain_tx_metrics else ["category", "status"]
    discovery_fields = sorted({k for row in discovery_metrics for k in row.keys()}) if discovery_metrics else ["metric_type"]
    governance_fields = sorted({k for row in governance_metrics for k in row.keys()}) if governance_metrics else ["mode", "status"]

    write_csv(phase_csv, phase_metrics, phase_fields)
    write_csv(chain_csv, chain_tx_metrics, chain_fields)
    write_csv(discovery_csv, discovery_metrics, discovery_fields)
    write_csv(governance_csv, governance_metrics, governance_fields)

    with raw_json.open("w", encoding="utf-8") as f:
        json.dump(raw_metrics, f, ensure_ascii=False, indent=2)

    summary_text = build_summary_markdown(
        phase_metrics=phase_metrics,
        chain_tx_metrics=chain_tx_metrics,
        discovery_metrics=discovery_metrics,
        governance_metrics=governance_metrics,
        usd_per_eth=usd_per_eth,
    )
    summary_md.write_text(summary_text, encoding="utf-8")

    return {
        "phase_metrics.csv": str(phase_csv),
        "chain_tx_metrics.csv": str(chain_csv),
        "discovery_metrics.csv": str(discovery_csv),
        "governance_metrics.csv": str(governance_csv),
        "raw_metrics.json": str(raw_json),
        "summary.md": str(summary_md),
    }

