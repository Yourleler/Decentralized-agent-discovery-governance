"""
MCP 互操作测试独立运行入口。

用途：无需拉起完整 fullflow（无需 Sepolia、无需 Holder 进程），
直接运行 MCP 互操作测试，输出用例断言 CSV + 论文图表。

运行方式：
  python fullflow_tests/run_mcp_tests.py
"""

from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

_current_dir = Path(__file__).resolve().parent
_project_root = _current_dir.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from fullflow_tests.mcp_interop import run_mcp_interop_flow
from fullflow_tests.mcp_visualization import generate_mcp_charts


def main() -> int:
    print("=" * 60)
    print("  MCP 互操作专项测试")
    print("=" * 60)

    # 加载默认配置
    config_path = _current_dir / "config.default.json"
    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    # 创建输出目录
    run_id = time.strftime("%Y%m%d_%H%M%S")
    run_dir = _current_dir / "results" / f"mcp_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # 执行测试
    result = run_mcp_interop_flow(config=config, run_dir=run_dir)
    case_assertions = result.get("case_assertions", [])
    mcp_metrics = result.get("mcp_metrics", [])

    # 写入断言 CSV
    if case_assertions:
        csv_path = run_dir / "mcp_case_assertions.csv"
        fieldnames = ["case_id", "capability_id", "phase", "passed", "expected", "actual", "error"]
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(case_assertions)
        print(f"\n[OK] 用例断言: {csv_path}")

    # 写入指标 CSV
    if mcp_metrics:
        metrics_path = run_dir / "mcp_metrics.csv"
        fieldnames = ["case_id", "metric_name", "value", "unit", "phase", "server_id", "tool_name", "stat_scope"]
        with metrics_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(mcp_metrics)
        print(f"[OK] 延迟指标: {metrics_path}")

    # 生成图表
    chart_files = generate_mcp_charts(run_dir, case_assertions, mcp_metrics)
    for cf in chart_files:
        print(f"[OK] 图表: {cf}")

    # 生成文本摘要
    total = len(case_assertions)
    passed = sum(1 for a in case_assertions if a.get("passed"))
    summary_path = run_dir / "mcp_summary.md"
    with summary_path.open("w", encoding="utf-8") as f:
        f.write("# MCP 互操作测试摘要\n\n")
        f.write(f"- 运行时间: {run_id}\n")
        f.write(f"- 总用例数: {total}\n")
        f.write(f"- 通过数: {passed}\n")
        f.write(f"- 通过率: {passed/total*100:.1f}%\n\n")

        f.write("## 用例明细\n\n")
        f.write("| 用例 | 类别 | 结果 | 说明 |\n")
        f.write("|------|------|------|------|\n")
        for a in case_assertions:
            status = "[OK] 通过" if a.get("passed") else "❌ 失败"
            actual = str(a.get("actual", ""))[:60]
            f.write(f"| {a.get('case_id', '')} | {a.get('capability_id', '')} | {status} | {actual} |\n")

        if mcp_metrics:
            f.write("\n## 延迟统计\n\n")
            batch = [m for m in mcp_metrics if m.get("stat_scope") == "batch"]
            if batch:
                f.write("| 指标 | 值 |\n")
                f.write("|------|----|\n")
                for m in batch:
                    f.write(f"| {m.get('metric_name', '')} | {float(m.get('value', 0))*1000:.2f} ms |\n")

    print(f"[OK] 摘要: {summary_path}")

    # 最终统计
    print(f"\n{'='*60}")
    print(f"  结果: {passed}/{total} 通过 ({passed/total*100:.1f}%)")
    print(f"  输出: {run_dir}")
    print(f"{'='*60}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
