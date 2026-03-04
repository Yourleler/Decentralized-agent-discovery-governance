from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from fullflow_tests.orchestrator import run_fullflow


def deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    功能：
    递归合并两个字典，返回合并后的新字典。

    参数：
    base (dict[str, Any]): 基础配置字典。
    override (dict[str, Any]): 覆盖配置字典。

    返回值：
    dict[str, Any]: 合并后的配置字典。
    """
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_json_file(path: Path) -> dict[str, Any]:
    """
    功能：
    读取 JSON 文件并解析为字典对象。

    参数：
    path (Path): JSON 文件路径。

    返回值：
    dict[str, Any]: 解析后的配置字典。
    """
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"配置文件不是对象类型: {path}")
    return data


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """
    功能：
    解析命令行参数并返回参数对象。

    参数：
    argv (list[str] | None): 可选命令行参数列表；为空时读取系统参数。

    返回值：
    argparse.Namespace: 解析后的参数对象。
    """
    parser = argparse.ArgumentParser(description="运行 fullflow 全流程闭环测试")
    parser.add_argument(
        "--config",
        default="fullflow_tests/config.default.json",
        help="配置文件路径",
    )
    parser.add_argument("--profile", default=None, help="运行档位")
    parser.add_argument("--rounds", type=int, default=None, help="审计轮次")
    parser.add_argument(
        "--account-strategy",
        default=None,
        choices=["mixed", "reuse", "fresh"],
        help="账户策略",
    )
    parser.add_argument(
        "--governance-mode",
        default=None,
        choices=["sepolia", "local", "both", "off"],
        help="治理模式",
    )
    parser.add_argument("--output-dir", default=None, help="输出目录")
    parser.add_argument(
        "--discovery-bind-current",
        default=None,
        choices=["true", "false"],
        help="发现阶段是否强绑定本次账户",
    )
    return parser.parse_args(argv)


def build_runtime_config(args: argparse.Namespace) -> dict[str, Any]:
    """
    功能：
    基于默认配置与命令行参数构建运行时配置。

    参数：
    args (argparse.Namespace): 命令行参数对象。

    返回值：
    dict[str, Any]: 可直接传入编排器的配置字典。
    """
    config_path = Path(args.config).resolve()
    cfg = load_json_file(config_path)

    if args.profile:
        cfg["profile"] = args.profile
    if args.rounds is not None:
        cfg["rounds"] = args.rounds
    if args.account_strategy:
        cfg["account_strategy"] = args.account_strategy
    if args.governance_mode:
        cfg["governance_mode"] = args.governance_mode
    if args.output_dir:
        cfg["output_dir"] = args.output_dir
    if args.discovery_bind_current is not None:
        cfg["discovery_bind_current"] = args.discovery_bind_current.lower() == "true"
    return cfg


def main(argv: list[str] | None = None) -> int:
    """
    功能：
    CLI 主入口，读取配置并执行 fullflow 测试流程。

    参数：
    argv (list[str] | None): 可选命令行参数列表；为空时读取系统参数。

    返回值：
    int: 进程退出码，0 表示成功，1 表示失败。
    """
    args = parse_args(argv)
    config = build_runtime_config(args)
    result = run_fullflow(config)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("status") == "success":
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
