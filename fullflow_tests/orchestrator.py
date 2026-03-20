from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import random
import subprocess
import traceback
from typing import Any
import time

from fullflow_tests.discovery import run_discovery_flow
from fullflow_tests.governance import run_governance_flow
from fullflow_tests.provision import ensure_accounts
from fullflow_tests.reporting import write_reports
from fullflow_tests.verification import run_verification_flow


def load_json(path: Path) -> dict[str, Any]:
    """
    功能：
    从磁盘读取 JSON 文件并返回字典对象。

    参数：
    path (Path): JSON 文件路径。

    返回值：
    dict[str, Any]: 解析后的 JSON 字典。
    """
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"JSON 顶层不是对象: {path}")
    return data


def sanitize_sensitive_data(data: Any) -> Any:
    """
    功能：
    对对象中的敏感字段进行脱敏处理，避免落盘泄露密钥。

    参数：
    data (Any): 任意待脱敏对象，支持 dict/list/基础类型。

    返回值：
    Any: 脱敏后的对象副本。
    """
    sensitive_keys = {
        "private_key",
        "qwq_api_key",
        "subgraph_api_key",
        "pinata_jwt",
    }
    if isinstance(data, dict):
        output: dict[str, Any] = {}
        for key, value in data.items():
            if str(key) in sensitive_keys:
                output[key] = "***REDACTED***"
            else:
                output[key] = sanitize_sensitive_data(value)
        return output
    if isinstance(data, list):
        return [sanitize_sensitive_data(item) for item in data]
    return data


def build_run_directory(base_output_dir: str) -> Path:
    """
    功能：
    在输出根目录下创建带时间戳的运行目录。

    参数：
    base_output_dir (str): 输出根目录路径。

    返回值：
    Path: 本次运行目录的绝对路径。
    """
    from datetime import datetime

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(base_output_dir).resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def apply_profile_defaults(config: dict[str, Any]) -> dict[str, Any]:
    """
    功能：
    根据 profile 自动补齐默认参数，支持论文复测档位。

    参数：
    config (dict[str, Any]): 原始配置字典。

    返回值：
    dict[str, Any]: 补齐后的配置字典副本。
    """
    cfg = dict(config)
    profile = str(cfg.get("profile", "standard")).strip().lower()
    if profile == "paper":
        cfg.setdefault("rounds", 3)
        cfg.setdefault("account_strategy", "mixed")
        cfg.setdefault("governance_mode", "both")
        cfg.setdefault("discovery_bind_current", True)
        cfg.setdefault("random_seed", 20260307)
    return cfg


def detect_git_commit() -> str:
    """
    功能：
    获取当前仓库短 commit id，失败时返回空字符串。

    参数：
    无。

    返回值：
    str: 短 commit id 或空字符串。
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            return (proc.stdout or "").strip()
    except Exception:
        return ""
    return ""


def build_run_manifest(config: dict[str, Any], run_dir: Path, random_seed: int) -> dict[str, Any]:
    """
    功能：
    生成运行清单对象，记录复测所需核心信息。

    参数：
    config (dict[str, Any]): 运行配置。
    run_dir (Path): 本次运行目录。
    random_seed (int): 随机种子。

    返回值：
    dict[str, Any]: 运行清单字典。
    """
    return {
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "profile": str(config.get("profile", "standard")),
        "rounds": int(config.get("rounds", 3)),
        "account_strategy": str(config.get("account_strategy", "mixed")),
        "governance_mode": str(config.get("governance_mode", "both")),
        "discovery_bind_current": bool(config.get("discovery_bind_current", True)),
        "output_dir": str(run_dir),
        "random_seed": int(random_seed),
        "git_commit": detect_git_commit(),
        "config_digest": {
            "provision": config.get("provision", {}),
            "discovery": config.get("discovery", {}),
            "verification": config.get("verification", {}),
            "governance": config.get("governance", {}),
            "reporting": config.get("reporting", {}),
        },
    }


def emit_progress(stage: str, message: str) -> None:
    """
    功能：
    以统一格式输出 fullflow 运行进度日志。

    参数：
    stage (str): 当前阶段标识。
    message (str): 进度消息文本。

    返回值：
    None: 仅打印日志，不返回数据。
    """
    ts = time.strftime("%H:%M:%S")
    print(f"[fullflow][{ts}][{stage}] {message}", flush=True)


def run_fullflow(config: dict[str, Any]) -> dict[str, Any]:
    """
    功能：
    执行 fullflow 全流程编排并生成统一结果对象。

    参数：
    config (dict[str, Any]): 运行配置字典。

    返回值：
    dict[str, Any]: 运行状态、输出路径与关键结果摘要。
    """
    runtime_config = apply_profile_defaults(config)
    output_root = str(runtime_config.get("output_dir", "fullflow_tests/results"))
    run_dir = build_run_directory(output_root)
    profile = str(runtime_config.get("profile", "standard")).strip().lower()
    default_seed = 20260307 if profile == "paper" else int(time.time())
    random_seed = int(runtime_config.get("random_seed", default_seed))
    random.seed(random_seed)
    manifest = build_run_manifest(runtime_config, run_dir, random_seed)
    manifest_path = run_dir / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    raw_metrics: dict[str, Any] = {
        "status": "running",
        "config": runtime_config,
        "run_dir": str(run_dir),
        "run_manifest_path": str(manifest_path),
        "run_manifest": manifest,
        "errors": [],
    }

    chain_tx_metrics: list[dict[str, Any]] = []
    phase_metrics: list[dict[str, Any]] = []
    discovery_metrics: list[dict[str, Any]] = []
    governance_metrics: list[dict[str, Any]] = []
    case_assertions: list[dict[str, Any]] = []

    provision_result: dict[str, Any] = {}
    discovery_result: dict[str, Any] = {}
    verification_result: dict[str, Any] = {}
    governance_result: dict[str, Any] = {}

    try:
        emit_progress("INIT", "加载根配置 config/key.json")
        root_key_config = load_json(Path("config/key.json").resolve())

        emit_progress("PROVISION", "开始账户准备")
        provision_result = ensure_accounts(
            config=runtime_config,
            root_key_config=root_key_config,
            run_dir=run_dir,
            chain_tx_metrics=chain_tx_metrics,
        )
        case_assertions.extend(provision_result.get("case_assertions", []))
        emit_progress("PROVISION", f"账户准备完成，策略={provision_result.get('strategy_used')}")

        emit_progress("DISCOVERY", "开始发现闭环（注册 + Subgraph + Sidecar）")
        discovery_result = run_discovery_flow(
            config=runtime_config,
            key_config=provision_result["key_config"],
            root_key_config=root_key_config,
            run_dir=run_dir,
            chain_tx_metrics=chain_tx_metrics,
        )
        discovery_metrics.extend(discovery_result.get("discovery_metrics", []))
        case_assertions.extend(discovery_result.get("case_assertions", []))
        emit_progress("DISCOVERY", "发现闭环完成")

        emit_progress("VERIFICATION", "开始 2v2 验证闭环")
        verification_result = run_verification_flow(
            config=runtime_config,
            key_config=provision_result["key_config"],
            key_path=provision_result["key_path"],
            run_dir=run_dir,
        )
        phase_metrics.extend(verification_result.get("phase_metrics", []))
        case_assertions.extend(verification_result.get("case_assertions", []))
        emit_progress("VERIFICATION", "验证闭环完成")

        emit_progress("GOVERNANCE", "开始治理闭环")
        governance_result = run_governance_flow(
            config=runtime_config,
            root_key_config=root_key_config,
            discovery_result=discovery_result,
            evidence_items=verification_result.get("evidence_items", []),
            run_dir=run_dir,
            chain_tx_metrics=chain_tx_metrics,
        )
        governance_metrics.extend(governance_result.get("governance_metrics", []))
        case_assertions.extend(governance_result.get("case_assertions", []))
        emit_progress("GOVERNANCE", "治理闭环完成")

        raw_metrics["status"] = "success"
    except Exception as exc:
        raw_metrics["status"] = "failed"
        emit_progress("ERROR", str(exc))
        raw_metrics["errors"].append(
            {
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
    finally:
        raw_metrics["provision"] = sanitize_sensitive_data(provision_result)
        raw_metrics["discovery"] = sanitize_sensitive_data(discovery_result)
        raw_metrics["verification"] = sanitize_sensitive_data(verification_result)
        raw_metrics["governance"] = sanitize_sensitive_data(governance_result)
        raw_metrics["case_assertions"] = sanitize_sensitive_data(case_assertions)

        report_result = write_reports(
            run_dir=run_dir,
            phase_metrics=phase_metrics,
            chain_tx_metrics=chain_tx_metrics,
            discovery_metrics=discovery_metrics,
            governance_metrics=governance_metrics,
            case_assertions=case_assertions,
            raw_metrics=raw_metrics,
            usd_per_eth=float(runtime_config.get("reporting", {}).get("usd_per_eth", 2930.0)),
            reporting_config=dict(runtime_config.get("reporting", {})),
        )
        emit_progress("REPORT", f"报表已生成: {run_dir}")

    return {
        "status": raw_metrics["status"],
        "run_dir": str(run_dir),
        "reports": report_result,
        "errors": raw_metrics.get("errors", []),
    }
