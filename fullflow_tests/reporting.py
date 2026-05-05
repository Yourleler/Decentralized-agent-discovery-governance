
from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from fullflow_tests.visualization import generate_charts
except Exception:

    def generate_charts(  # type: ignore[no-redef]
        run_dir: Path,
        phase_metrics: list[dict[str, Any]],
        latency_stats_rows: list[dict[str, Any]],
        case_assertions: list[dict[str, Any]],
        chain_tx_rows: list[dict[str, Any]],
        l2_summary_rows: list[dict[str, Any]],
        scale_projection_rows: list[dict[str, Any]],
    ) -> dict[str, str]:
        _ = (
            run_dir,
            phase_metrics,
            latency_stats_rows,
            case_assertions,
            chain_tx_rows,
            l2_summary_rows,
            scale_projection_rows,
        )
        return {}

try:
    from fullflow_tests.mcp_visualization import generate_mcp_charts
except Exception:

    def generate_mcp_charts(  # type: ignore[no-redef]
        run_dir: Path,
        case_assertions: list[dict[str, Any]],
        mcp_metrics: list[dict[str, Any]],
    ) -> list[str]:
        _ = (run_dir, case_assertions, mcp_metrics)
        return []


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(sum(values)) / float(len(values))


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if q <= 0:
        return float(min(values))
    if q >= 1:
        return float(max(values))
    ordered = sorted(float(item) for item in values)
    pos = (len(ordered) - 1) * q
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    if low == high:
        return ordered[low]
    weight = pos - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _append_latency_row(
    rows: list[dict[str, Any]],
    scope: str,
    stage: str,
    case_key: str,
    values: list[float],
) -> None:
    cleaned = [float(item) for item in values if float(item) >= 0]
    rows.append(
        {
            "stat_scope": scope,
            "latency_stage": stage,
            "case_key": case_key,
            "count": len(cleaned),
            "mean_seconds": mean(cleaned),
            "p50_seconds": percentile(cleaned, 0.50),
            "p95_seconds": percentile(cleaned, 0.95),
            "max_seconds": max(cleaned) if cleaned else 0.0,
        }
    )


def normalize_case_assertions(case_assertions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in case_assertions:
        row = dict(item)
        row["phase"] = str(row.get("phase", "unknown"))
        row["case_id"] = str(row.get("case_id", ""))
        row["capability_id"] = str(row.get("capability_id", ""))
        row["expected"] = str(row.get("expected", ""))
        row["actual"] = str(row.get("actual", ""))
        row["passed"] = bool(row.get("passed", False))
        row["error"] = str(row.get("error", ""))
        normalized.append(row)
    return normalized


def enrich_chain_tx_metrics(
    chain_tx_metrics: list[dict[str, Any]],
    usd_per_eth: float,
    usd_cny_rate: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in chain_tx_metrics:
        row = dict(item)
        category = str(row.get("category", ""))
        phase = str(row.get("phase") or (category.split("_")[0] if "_" in category else "unknown"))
        cost_eth = safe_float(row.get("cost_eth"), 0.0)
        row["phase"] = phase
        row["case_id"] = str(row.get("case_id", ""))
        row["chain"] = str(row.get("chain", "evm"))
        row["network"] = str(row.get("network", "sepolia"))
        row["tx_type"] = str(row.get("tx_type", category or "unknown"))
        row["cost_eth"] = cost_eth
        row["cost_usd"] = cost_eth * usd_per_eth
        row["cost_cny"] = cost_eth * usd_per_eth * usd_cny_rate
        row["latency_seconds"] = safe_float(row.get("latency_seconds"), 0.0)
        rows.append(row)
    return rows


def build_latency_stats_rows(
    phase_metrics: list[dict[str, Any]],
    discovery_metrics: list[dict[str, Any]],
    governance_metrics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    positive_rows = [row for row in phase_metrics if row.get("scenario") == "positive" and row.get("status") == "passed"]
    stage_map = {
        "T4_auth": [safe_float(row.get("T4")) for row in positive_rows if safe_float(row.get("T4")) > 0],
        "T8_probe": [safe_float(row.get("T8")) for row in positive_rows if safe_float(row.get("T8")) > 0],
        "T12_context": [safe_float(row.get("T12")) for row in positive_rows if safe_float(row.get("T12")) > 0],
        "Total": [safe_float(row.get("Total_Duration")) for row in positive_rows if safe_float(row.get("Total_Duration")) > 0],
    }
    for stage_name, values in stage_map.items():
        _append_latency_row(rows, scope="stage", stage=stage_name, case_key="ALL", values=values)

    grouped_total: dict[str, list[float]] = defaultdict(list)
    for row in positive_rows:
        pair_name = str(row.get("pair_name", "unknown"))
        duration = safe_float(row.get("Total_Duration"), 0.0)
        if duration > 0:
            grouped_total[pair_name].append(duration)
    for pair_name, values in grouped_total.items():
        _append_latency_row(rows, scope="case", stage="Total", case_key=pair_name, values=values)

    stress_task_rows = [row for row in phase_metrics if str(row.get("scenario")) == "concurrency_stress_task"]
    _append_latency_row(
        rows,
        scope="metric",
        stage="Concurrency_Stress_Total",
        case_key="ALL",
        values=[
            safe_float(row.get("Total_Duration"), 0.0)
            for row in stress_task_rows
            if safe_float(row.get("Total_Duration"), 0.0) > 0
        ],
    )
    stress_summary_rows = [row for row in phase_metrics if str(row.get("scenario")) == "concurrency_stress_summary"]
    for stress_summary in stress_summary_rows:
        level_name = str(stress_summary.get("load_level", "L1"))
        rows.append(
            {
                "stat_scope": "concurrency_summary",
                "latency_stage": "Concurrency_Stress",
                "case_key": level_name,
                "count": int(safe_float(stress_summary.get("total_tasks"), 0)),
                "mean_seconds": safe_float(stress_summary.get("avg_duration_seconds"), 0.0),
                "p50_seconds": safe_float(stress_summary.get("p50_duration_seconds"), 0.0),
                "p95_seconds": safe_float(stress_summary.get("p95_duration_seconds"), 0.0),
                "max_seconds": safe_float(stress_summary.get("max_duration_seconds"), 0.0),
                "pass_rate": safe_float(stress_summary.get("pass_rate"), 0.0),
                "throughput_tps": safe_float(stress_summary.get("throughput_tps"), 0.0),
                "failed_tasks": int(safe_float(stress_summary.get("failed_tasks"), 0)),
                "workers": int(safe_float(stress_summary.get("max_workers"), 0)),
                "tasks_per_pair": int(safe_float(stress_summary.get("tasks_per_pair"), 0)),
                "load_level": level_name,
            }
        )
    if stress_summary_rows:
        total_tasks_all = sum(int(safe_float(row.get("total_tasks"), 0)) for row in stress_summary_rows)
        passed_tasks_all = sum(int(safe_float(row.get("passed_tasks"), 0)) for row in stress_summary_rows)
        pass_rate_all = (float(passed_tasks_all) / float(max(1, total_tasks_all))) if total_tasks_all > 0 else 0.0
        rows.append(
            {
                "stat_scope": "concurrency_summary_aggregate",
                "latency_stage": "Concurrency_Stress",
                "case_key": "ALL",
                "count": total_tasks_all,
                "mean_seconds": mean([safe_float(row.get("avg_duration_seconds"), 0.0) for row in stress_summary_rows]),
                "p50_seconds": mean([safe_float(row.get("p50_duration_seconds"), 0.0) for row in stress_summary_rows]),
                "p95_seconds": max([safe_float(row.get("p95_duration_seconds"), 0.0) for row in stress_summary_rows]),
                "max_seconds": max([safe_float(row.get("max_duration_seconds"), 0.0) for row in stress_summary_rows]),
                "pass_rate": pass_rate_all,
                "throughput_tps": sum([safe_float(row.get("throughput_tps"), 0.0) for row in stress_summary_rows]),
                "failed_tasks": max(total_tasks_all - passed_tasks_all, 0),
                "workers": max([int(safe_float(row.get("max_workers"), 0)) for row in stress_summary_rows] + [0]),
                "tasks_per_pair": max([int(safe_float(row.get("tasks_per_pair"), 0)) for row in stress_summary_rows] + [0]),
                "load_level": "ALL",
            }
        )

    search_rows = [row for row in discovery_metrics if str(row.get("metric_type")) == "search_assertion"]
    _append_latency_row(
        rows,
        scope="metric",
        stage="Discovery_Query_Total",
        case_key="ALL",
        values=[safe_float(row.get("query_latency_ms"), 0.0) / 1000.0 for row in search_rows if safe_float(row.get("query_latency_ms"), 0.0) >= 0],
    )
    _append_latency_row(
        rows,
        scope="metric",
        stage="Vector_Match",
        case_key="ALL",
        values=[safe_float(row.get("vector_match_latency_ms"), 0.0) / 1000.0 for row in search_rows if safe_float(row.get("vector_match_latency_ms"), 0.0) >= 0],
    )

    cid_rows = [row for row in discovery_metrics if str(row.get("metric_type")) == "cid_io"] + [
        row for row in governance_metrics if str(row.get("action")) == "cid_io" and str(row.get("mode")) == "local"
    ]
    _append_latency_row(
        rows,
        scope="metric",
        stage="CID_Upload",
        case_key="ALL",
        values=[safe_float(row.get("io_seconds"), 0.0) for row in cid_rows if str(row.get("io_direction")) == "upload" and safe_float(row.get("io_seconds"), 0.0) >= 0],
    )
    _append_latency_row(
        rows,
        scope="metric",
        stage="CID_Download",
        case_key="ALL",
        values=[safe_float(row.get("io_seconds"), 0.0) for row in cid_rows if str(row.get("io_direction")) == "download" and safe_float(row.get("io_seconds"), 0.0) >= 0],
    )

    sepolia_rows = [
        row
        for row in governance_metrics
        if str(row.get("mode")) == "sepolia" and str(row.get("action")) == "reportMisbehavior"
    ]
    _append_latency_row(
        rows,
        scope="metric",
        stage="Governance_Report_Onchain",
        case_key="ALL",
        values=[safe_float(row.get("latency_seconds"), 0.0) for row in sepolia_rows if safe_float(row.get("latency_seconds"), 0.0) > 0],
    )

    return rows


def default_l2_profiles() -> dict[str, dict[str, float]]:
    return {
        "base": {"gas_price_gwei": 0.03, "l1_data_fee_eth_per_tx": 0.000003},
        "optimism": {"gas_price_gwei": 0.02, "l1_data_fee_eth_per_tx": 0.000004},
        "arbitrum": {"gas_price_gwei": 0.01, "l1_data_fee_eth_per_tx": 0.000002},
    }


def estimate_l2_costs(
    chain_tx_rows: list[dict[str, Any]],
    usd_per_eth: float,
    usd_cny_rate: float,
    l2_profiles: dict[str, dict[str, float]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    detail_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    for row in chain_tx_rows:
        gas_used = int(safe_float(row.get("gas_used"), 0.0))
        if gas_used <= 0:
            continue
        for l2_name, profile in l2_profiles.items():
            gas_price_gwei = safe_float(profile.get("gas_price_gwei"), 0.0)
            l1_data_fee_eth_per_tx = safe_float(profile.get("l1_data_fee_eth_per_tx"), 0.0)
            l2_cost_eth = gas_used * gas_price_gwei * 1e-9 + l1_data_fee_eth_per_tx
            cost_usd = l2_cost_eth * usd_per_eth
            cost_cny = cost_usd * usd_cny_rate
            detail_rows.append(
                {
                    "l2_name": l2_name,
                    "scope": "per_tx",
                    "tx_hash": row.get("tx_hash", ""),
                    "case_id": row.get("case_id", ""),
                    "phase": row.get("phase", ""),
                    "tx_type": row.get("tx_type", ""),
                    "gas_used": gas_used,
                    "gas_price_gwei": gas_price_gwei,
                    "l1_data_fee_eth_per_tx": l1_data_fee_eth_per_tx,
                    "l2_cost_eth": l2_cost_eth,
                    "cost_usd": cost_usd,
                    "cost_cny": cost_cny,
                }
            )

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in detail_rows:
        grouped[str(row.get("l2_name", ""))].append(row)

    for l2_name, rows in grouped.items():
        tx_count = len(rows)
        total_eth = sum(safe_float(item.get("l2_cost_eth"), 0.0) for item in rows)
        total_usd = sum(safe_float(item.get("cost_usd"), 0.0) for item in rows)
        total_cny = sum(safe_float(item.get("cost_cny"), 0.0) for item in rows)
        summary_rows.append(
            {
                "l2_name": l2_name,
                "scope": "summary",
                "tx_hash": "__TOTAL__",
                "case_id": "",
                "phase": "",
                "tx_type": "ALL",
                "gas_used": "",
                "gas_price_gwei": "",
                "l1_data_fee_eth_per_tx": "",
                "l2_cost_eth": total_eth,
                "cost_usd": total_usd,
                "cost_cny": total_cny,
                "tx_count": tx_count,
                "avg_cost_cny_per_tx": (total_cny / tx_count) if tx_count > 0 else 0.0,
            }
        )

    return detail_rows + summary_rows, summary_rows

def _parse_scale_targets(scale_cfg: dict[str, Any]) -> list[int]:
    raw = scale_cfg.get("agent_counts", [100, 500, 1000])
    if not isinstance(raw, list):
        return [100, 500, 1000]
    targets: list[int] = []
    for item in raw:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value > 0:
            targets.append(value)
    if not targets:
        return [100, 500, 1000]
    return sorted(set(targets))


def build_scale_projection_rows(
    phase_metrics: list[dict[str, Any]],
    discovery_metrics: list[dict[str, Any]],
    governance_metrics: list[dict[str, Any]],
    chain_tx_rows: list[dict[str, Any]],
    l2_summary_rows: list[dict[str, Any]],
    usd_per_eth: float,
    usd_cny_rate: float,
    reporting_config: dict[str, Any],
) -> list[dict[str, Any]]:
    scale_cfg = reporting_config.get("scale_projection", {})
    if not isinstance(scale_cfg, dict):
        scale_cfg = {}
    if scale_cfg.get("enabled", True) is False:
        return []

    targets = _parse_scale_targets(scale_cfg)

    search_rows = [row for row in discovery_metrics if str(row.get("metric_type")) == "search_assertion"]
    observed_agents = len({str(row.get("target_agent", "")).strip().lower() for row in search_rows if str(row.get("target_agent", "")).strip()})
    baseline_from_cfg = int(safe_float(scale_cfg.get("baseline_agents"), 0))
    baseline_agents = baseline_from_cfg if baseline_from_cfg > 0 else (observed_agents if observed_agents > 0 else 2)

    positive_rows = [row for row in phase_metrics if row.get("scenario") == "positive" and row.get("status") == "passed"]
    avg_verify_seconds = mean([safe_float(row.get("Total_Duration"), 0.0) for row in positive_rows if safe_float(row.get("Total_Duration"), 0.0) > 0])
    avg_query_seconds = mean([safe_float(row.get("query_latency_ms"), 0.0) / 1000.0 for row in search_rows if safe_float(row.get("query_latency_ms"), 0.0) >= 0])
    avg_vector_seconds = mean([safe_float(row.get("vector_match_latency_ms"), 0.0) / 1000.0 for row in search_rows if safe_float(row.get("vector_match_latency_ms"), 0.0) >= 0])

    cid_rows = [row for row in discovery_metrics if str(row.get("metric_type")) == "cid_io"] + [
        row for row in governance_metrics if str(row.get("action")) == "cid_io" and str(row.get("mode")) == "local"
    ]
    avg_cid_upload_seconds = mean([safe_float(row.get("io_seconds"), 0.0) for row in cid_rows if str(row.get("io_direction")) == "upload" and safe_float(row.get("io_seconds"), 0.0) >= 0])
    avg_cid_download_seconds = mean([safe_float(row.get("io_seconds"), 0.0) for row in cid_rows if str(row.get("io_direction")) == "download" and safe_float(row.get("io_seconds"), 0.0) >= 0])

    avg_chain_latency_seconds = mean([safe_float(row.get("latency_seconds"), 0.0) for row in chain_tx_rows if safe_float(row.get("latency_seconds"), 0.0) > 0])
    avg_chain_cost_eth = mean([safe_float(row.get("cost_eth"), 0.0) for row in chain_tx_rows if safe_float(row.get("cost_eth"), 0.0) > 0])

    l2_avg_cost_cny_map: dict[str, float] = {
        str(row.get("l2_name", "")): safe_float(row.get("avg_cost_cny_per_tx"), 0.0)
        for row in l2_summary_rows
        if str(row.get("l2_name", "")).strip()
    }

    observed_query_count = max(1, len(search_rows))
    observed_chain_tx_count = max(1, len(chain_tx_rows))

    rows: list[dict[str, Any]] = []
    for target_agents in targets:
        scale_ratio = float(target_agents) / float(max(1, baseline_agents))
        projected_queries = observed_query_count * scale_ratio
        projected_chain_txs = observed_chain_tx_count * scale_ratio

        est_verification_seconds = avg_verify_seconds * float(target_agents)
        est_discovery_query_seconds = avg_query_seconds * projected_queries
        est_vector_match_seconds = avg_vector_seconds * projected_queries
        est_cid_io_seconds = (avg_cid_upload_seconds + avg_cid_download_seconds) * float(target_agents)
        est_chain_latency_seconds = avg_chain_latency_seconds * projected_chain_txs

        est_chain_cost_eth = avg_chain_cost_eth * projected_chain_txs
        est_chain_cost_usd = est_chain_cost_eth * usd_per_eth
        est_chain_cost_cny = est_chain_cost_usd * usd_cny_rate

        row: dict[str, Any] = {
            "target_agents": int(target_agents),
            "baseline_agents": int(baseline_agents),
            "scale_ratio": scale_ratio,
            "projected_queries": projected_queries,
            "projected_chain_txs": projected_chain_txs,
            "avg_verify_seconds_per_agent": avg_verify_seconds,
            "avg_query_seconds": avg_query_seconds,
            "avg_vector_match_seconds": avg_vector_seconds,
            "avg_cid_upload_seconds": avg_cid_upload_seconds,
            "avg_cid_download_seconds": avg_cid_download_seconds,
            "avg_chain_latency_seconds": avg_chain_latency_seconds,
            "avg_chain_cost_eth": avg_chain_cost_eth,
            "est_verification_seconds": est_verification_seconds,
            "est_discovery_query_seconds": est_discovery_query_seconds,
            "est_vector_match_seconds": est_vector_match_seconds,
            "est_cid_io_seconds": est_cid_io_seconds,
            "est_chain_latency_seconds": est_chain_latency_seconds,
            "est_chain_cost_eth": est_chain_cost_eth,
            "est_chain_cost_usd": est_chain_cost_usd,
            "est_chain_cost_cny": est_chain_cost_cny,
        }

        row["est_total_time_seconds"] = est_verification_seconds + est_discovery_query_seconds + est_vector_match_seconds + est_cid_io_seconds + est_chain_latency_seconds
        row["est_total_cost_cny"] = est_chain_cost_cny

        for l2_name, avg_cny in sorted(l2_avg_cost_cny_map.items()):
            row[f"est_l2_{l2_name}_cost_cny"] = avg_cny * projected_chain_txs

        rows.append(row)

    return rows


def build_chain_action_projection_rows(
    chain_tx_rows: list[dict[str, Any]],
    scale_projection_rows: list[dict[str, Any]],
    usd_per_eth: float,
    usd_cny_rate: float,
) -> list[dict[str, Any]]:
    if not chain_tx_rows or not scale_projection_rows:
        return []

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in chain_tx_rows:
        grouped[str(row.get("tx_type", "unknown"))].append(row)

    rows: list[dict[str, Any]] = []
    baseline_agents = int(scale_projection_rows[0].get("baseline_agents", 1) or 1)
    for target in scale_projection_rows:
        target_agents = int(target.get("target_agents", baseline_agents) or baseline_agents)
        scale_ratio = float(target_agents) / float(max(1, baseline_agents))
        for tx_type, items in grouped.items():
            observed_count = len(items)
            avg_cost_eth = mean([safe_float(item.get("cost_eth"), 0.0) for item in items])
            avg_latency_seconds = mean([safe_float(item.get("latency_seconds"), 0.0) for item in items])
            projected_count = observed_count * scale_ratio
            est_cost_eth = avg_cost_eth * projected_count
            est_cost_usd = est_cost_eth * usd_per_eth
            est_cost_cny = est_cost_usd * usd_cny_rate
            est_latency_seconds = avg_latency_seconds * projected_count
            rows.append(
                {
                    "target_agents": target_agents,
                    "tx_type": tx_type,
                    "observed_count": observed_count,
                    "scale_ratio": scale_ratio,
                    "projected_count": projected_count,
                    "avg_cost_eth": avg_cost_eth,
                    "avg_latency_seconds": avg_latency_seconds,
                    "est_cost_eth": est_cost_eth,
                    "est_cost_usd": est_cost_usd,
                    "est_cost_cny": est_cost_cny,
                    "est_latency_seconds": est_latency_seconds,
                }
            )
    return rows


def default_operation_estimation() -> dict[str, Any]:
    return {
        "action_counts": {
            "discovery_register_agent": 2,
            "discovery_update_metadata": 2,
            "governance_report_misbehavior": 1,
            "governance_appeal": 1,
            "governance_freeze": 1,
            "governance_unfreeze": 1,
            "governance_slash": 1,
            "governance_restore": 1,
        },
        "gas_defaults": {
            "discovery_register_agent": 280000,
            "discovery_update_metadata": 120000,
            "governance_report_misbehavior": 300000,
            "governance_appeal": 130000,
            "governance_freeze": 90000,
            "governance_unfreeze": 90000,
            "governance_slash": 150000,
            "governance_restore": 110000,
        },
    }


def build_l2_operation_estimate_rows(
    chain_tx_rows: list[dict[str, Any]],
    usd_per_eth: float,
    usd_cny_rate: float,
    l2_profiles: dict[str, dict[str, float]],
    reporting_config: dict[str, Any],
) -> list[dict[str, Any]]:
    op_cfg = default_operation_estimation()
    cfg = reporting_config.get("operation_estimation")
    if isinstance(cfg, dict):
        counts = cfg.get("action_counts")
        if isinstance(counts, dict):
            for key, value in counts.items():
                try:
                    op_cfg["action_counts"][str(key)] = max(0, int(value))
                except (TypeError, ValueError):
                    continue
        gas_defaults = cfg.get("gas_defaults")
        if isinstance(gas_defaults, dict):
            for key, value in gas_defaults.items():
                try:
                    op_cfg["gas_defaults"][str(key)] = max(0, int(value))
                except (TypeError, ValueError):
                    continue

    observed: dict[str, list[int]] = defaultdict(list)
    for row in chain_tx_rows:
        tx_type = str(row.get("tx_type", "")).strip()
        gas_used = int(safe_float(row.get("gas_used"), 0.0))
        if tx_type and gas_used > 0:
            observed[tx_type].append(gas_used)

    all_actions = sorted(set(op_cfg["action_counts"].keys()) | set(op_cfg["gas_defaults"].keys()) | set(observed.keys()))

    rows: list[dict[str, Any]] = []
    for action in all_actions:
        observed_count = len(observed.get(action, []))
        action_count = int(op_cfg["action_counts"].get(action, observed_count))
        if observed_count > 0 and action_count <= 0:
            action_count = observed_count
        if action_count <= 0:
            continue

        if observed_count > 0:
            gas_used = int(round(mean([float(item) for item in observed[action]])))
            gas_source = "observed"
        else:
            gas_used = int(op_cfg["gas_defaults"].get(action, 0))
            gas_source = "default"
        if gas_used <= 0:
            continue

        for l2_name, profile in l2_profiles.items():
            gas_price_gwei = safe_float(profile.get("gas_price_gwei"), 0.0)
            l1_data_fee_eth_per_tx = safe_float(profile.get("l1_data_fee_eth_per_tx"), 0.0)
            per_tx_eth = gas_used * gas_price_gwei * 1e-9 + l1_data_fee_eth_per_tx
            per_tx_usd = per_tx_eth * usd_per_eth
            per_tx_cny = per_tx_usd * usd_cny_rate
            rows.append(
                {
                    "operation_id": action,
                    "l2_name": l2_name,
                    "action_count": action_count,
                    "observed_tx_count": observed_count,
                    "gas_used": gas_used,
                    "gas_source": gas_source,
                    "gas_price_gwei": gas_price_gwei,
                    "l1_data_fee_eth_per_tx": l1_data_fee_eth_per_tx,
                    "per_tx_cost_eth": per_tx_eth,
                    "per_tx_cost_usd": per_tx_usd,
                    "per_tx_cost_cny": per_tx_cny,
                    "total_cost_eth": per_tx_eth * action_count,
                    "total_cost_usd": per_tx_usd * action_count,
                    "total_cost_cny": per_tx_cny * action_count,
                }
            )
    return rows

def build_failed_summary_markdown(
    raw_metrics: dict[str, Any],
    usd_per_eth: float,
    usd_cny_rate: float,
    l2_operation_rows: list[dict[str, Any]],
) -> str:
    status = str(raw_metrics.get("status", "failed"))
    errors = raw_metrics.get("errors", [])
    error_lines: list[str] = []
    if isinstance(errors, list):
        for item in errors:
            if not isinstance(item, dict):
                continue
            phase = str(item.get("phase", "")).strip()
            message = str(item.get("message", "")).strip()
            if message:
                if phase:
                    error_lines.append(f"- [{phase}] {message}")
                else:
                    error_lines.append(f"- {message}")
    if not error_lines:
        error_lines.append("- 未记录结构化错误详情，请检查 raw_metrics.json。")

    grouped_ops: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in l2_operation_rows:
        op = str(row.get("operation_id", ""))
        l2_name = str(row.get("l2_name", ""))
        if op and l2_name:
            grouped_ops[op][l2_name] = row

    l2_op_lines: list[str] = []
    for op in sorted(grouped_ops.keys()):
        row_map = grouped_ops[op]
        base = safe_float(row_map.get("base", {}).get("per_tx_cost_cny"), 0.0)
        optimism = safe_float(row_map.get("optimism", {}).get("per_tx_cost_cny"), 0.0)
        arbitrum = safe_float(row_map.get("arbitrum", {}).get("per_tx_cost_cny"), 0.0)
        sample_row = next(iter(row_map.values()))
        action_count = int(safe_float(sample_row.get("action_count"), 0))
        gas_source = str(sample_row.get("gas_source", "default"))
        l2_op_lines.append(
            f"- {op}: 次数={action_count}，单笔估算[CNY] Base={base:.4f}, Optimism={optimism:.4f}, Arbitrum={arbitrum:.4f} (gas来源: {gas_source})"
        )
    if not l2_op_lines:
        l2_op_lines.append("- 无可用操作估算数据。")

    lines = [
        "# Fullflow 测试摘要（失败）",
        "",
        f"- 运行状态: {status}",
        f"- 汇率口径: 1 ETH = {usd_per_eth:.2f} USD, 1 USD = {usd_cny_rate:.4f} CNY",
        "",
        "## 失败原因",
    ]
    lines.extend(error_lines)
    lines.extend(
        [
            "",
            "## 指标状态",
            "- 本次运行未完成闭环，`phase_metrics.csv / discovery_metrics.csv / chain_tx_metrics.csv / case_assertions.csv` 无有效样本。",
            "- 验证延迟、TPS、发现命中率、链上实测成本统一记为 `N/A`（不再显示误导性的 0）。",
            "",
            "## L2 按操作估算（配置口径）",
        ]
    )
    lines.extend(l2_op_lines)
    lines.extend(
        [
            "",
            "## 说明",
            "- `l2_operation_estimates.csv` 为论文估算值，可用于方法论口径对比，不代表本次 run 的真实链上账单。",
            "- 请先修复失败原因后重跑，再引用实测指标。",
        ]
    )
    return "\n".join(lines)


def build_summary_markdown(
    phase_metrics: list[dict[str, Any]],
    chain_tx_metrics: list[dict[str, Any]],
    discovery_metrics: list[dict[str, Any]],
    governance_metrics: list[dict[str, Any]],
    case_assertions: list[dict[str, Any]],
    latency_stats_rows: list[dict[str, Any]],
    usd_per_eth: float,
    usd_cny_rate: float,
    l2_summary_rows: list[dict[str, Any]],
    l2_operation_rows: list[dict[str, Any]],
    scale_projection_rows: list[dict[str, Any]],
    chart_files: dict[str, str],
    raw_metrics: dict[str, Any] | None = None,
) -> str:
    raw = dict(raw_metrics or {})
    cfg = dict(raw.get("config", {}))
    run_status = str(raw.get("status", "unknown"))
    run_dir_text = str(raw.get("run_dir", ""))

    phase_order = ["provision", "discovery", "verification", "mcp_interop", "governance"]
    phase_stat_map: dict[str, dict[str, int]] = {}
    for phase in phase_order:
        phase_rows = [row for row in case_assertions if str(row.get("phase", "")) == phase]
        total = len(phase_rows)
        passed = sum(1 for row in phase_rows if bool(row.get("passed")))
        phase_stat_map[phase] = {
            "total": total,
            "passed": passed,
            "failed": max(total - passed, 0),
        }

    positive_rows = [
        row for row in phase_metrics
        if str(row.get("scenario")) == "positive" and str(row.get("status")) == "passed"
    ]
    round_rows = [row for row in phase_metrics if str(row.get("scenario")) == "round_summary"]
    tps_values = [safe_float(row.get("round_tps"), 0.0) for row in round_rows if safe_float(row.get("round_tps"), 0.0) > 0]

    stage_latency = {
        str(row.get("latency_stage", "")): row
        for row in latency_stats_rows
        if str(row.get("stat_scope", "")) == "stage"
    }
    metric_latency = {
        str(row.get("latency_stage", "")): row
        for row in latency_stats_rows
        if str(row.get("stat_scope", "")) == "metric"
    }
    stress_summaries = [
        row for row in phase_metrics if str(row.get("scenario")) == "concurrency_stress_summary"
    ]

    negative_rows = [row for row in phase_metrics if str(row.get("scenario")) == "negative"]
    mcp_rows = [row for row in case_assertions if str(row.get("phase")) == "mcp_interop"]
    mcp_total = len(mcp_rows)
    mcp_passed = sum(1 for row in mcp_rows if bool(row.get("passed")))
    governance_passed = sum(1 for row in governance_metrics if str(row.get("status")) == "passed")

    total_cost_eth = sum(safe_float(row.get("cost_eth"), 0.0) for row in chain_tx_metrics)
    total_cost_usd = total_cost_eth * usd_per_eth
    total_cost_cny = total_cost_usd * usd_cny_rate

    scale_note = "未启用规模外推"
    if scale_projection_rows:
        peak = max(scale_projection_rows, key=lambda item: int(safe_float(item.get("target_agents"), 0)))
        scale_note = (
            f"{int(safe_float(peak.get('target_agents'), 0))} agents 估算: "
            f"总时延 {safe_float(peak.get('est_total_time_seconds'), 0.0):.2f}s, "
            f"总成本 {safe_float(peak.get('est_total_cost_cny'), 0.0):.2f} CNY"
        )

    chart_desc_map = {
        "chart_latency_stage.png": "主链路阶段延迟（T4/T8/T12/Total）",
        "chart_security_negative_matrix.png": "安全负例通过/失败矩阵",
        "chart_concurrency_stress.png": "并发压测矩阵（按规模档位）",
        "chart_mcp_abuse_matrix.png": "MCP 并发越权拦截矩阵（按规模档位）",
        "chart_tx_cost_eth.png": "链上成本分解（按交易类型）",
        "chart_l2_cost_cny.png": "L2 成本估算",
        "chart_scale_projection.png": "规模外推（时间/成本）",
        "chart_mcp_latency_distribution.png": "MCP 批量调用延迟分布",
        "chart_mcp_latency_matrix.png": "MCP 并发延迟矩阵（按规模档位）",
        "chart_mcp_test_matrix.png": "MCP 用例分类通过率",
        "chart_mcp_tool_comparison.png": "MCP 单次工具调用延迟",
    }

    lines: list[str] = []
    lines.append("# Fullflow 全流程测试汇报")
    lines.append("")
    lines.append("## 1) 运行概览")
    lines.append(f"- 运行状态: `{run_status}`")
    if run_dir_text:
        lines.append(f"- 结果目录: `{run_dir_text}`")
    lines.append(
        f"- 配置: rounds={int(safe_float(cfg.get('rounds'), 0))}, "
        f"account_strategy={cfg.get('account_strategy', '')}, "
        f"governance_mode={cfg.get('governance_mode', '')}"
    )
    lines.append("")
    lines.append("## 2) 覆盖范围")
    lines.append("- Provision: 账户创建/复用、打币、DID 注册、Delegate 授权")
    lines.append("- Discovery: 链上注册/更新、Subgraph 收录、Sidecar 检索断言")
    lines.append("- Verification: 2v2 多轮正向审计 + S/M/L 并发矩阵 + 安全负例")
    lines.append("- MCP Interop: 连接、工具发现、调用、权限控制、批量延迟")
    lines.append("- Governance: Sepolia 举报 + 本地治理动作链路")
    lines.append("")
    lines.append("## 3) 分阶段结果")
    lines.append("| 阶段 | 断言总数 | 通过 | 失败 | 通过率 |")
    lines.append("|---|---:|---:|---:|---:|")
    for phase in phase_order:
        item = phase_stat_map.get(phase, {"total": 0, "passed": 0, "failed": 0})
        total = int(item["total"])
        passed = int(item["passed"])
        failed = int(item["failed"])
        rate = (passed / total * 100.0) if total > 0 else 0.0
        lines.append(f"| {phase} | {total} | {passed} | {failed} | {rate:.1f}% |")
    lines.append("")
    lines.append("## 4) 性能与并发")
    lines.append(
        f"- 主链路平均延迟: T4={safe_float(stage_latency.get('T4_auth', {}).get('mean_seconds')):.3f}s, "
        f"T8={safe_float(stage_latency.get('T8_probe', {}).get('mean_seconds')):.3f}s, "
        f"T12={safe_float(stage_latency.get('T12_context', {}).get('mean_seconds')):.3f}s, "
        f"Total={safe_float(stage_latency.get('Total', {}).get('mean_seconds')):.3f}s"
    )
    lines.append(
        f"- 主链路 P95 延迟: T4={safe_float(stage_latency.get('T4_auth', {}).get('p95_seconds')):.3f}s, "
        f"T8={safe_float(stage_latency.get('T8_probe', {}).get('p95_seconds')):.3f}s, "
        f"T12={safe_float(stage_latency.get('T12_context', {}).get('p95_seconds')):.3f}s, "
        f"Total={safe_float(stage_latency.get('Total', {}).get('p95_seconds')):.3f}s"
    )
    lines.append(f"- 轮次 TPS: avg={mean(tps_values):.4f}, max={max(tps_values) if tps_values else 0.0:.4f}")
    if stress_summaries:
        total_tasks = sum(int(safe_float(item.get("total_tasks"), 0)) for item in stress_summaries)
        total_passed = sum(int(safe_float(item.get("passed_tasks"), 0)) for item in stress_summaries)
        total_pass_rate = (float(total_passed) / float(max(1, total_tasks))) if total_tasks else 0.0
        lines.append(
            f"- 并发压测矩阵汇总: passed={total_passed}/{total_tasks}, pass_rate={total_pass_rate:.2%}, "
            f"levels={len(stress_summaries)}"
        )
        lines.append("- 并发压测分档:")
        for summary in stress_summaries:
            level_name = str(summary.get("load_level", "L1"))
            lines.append(
                f"  - [{level_name}] tasks={int(safe_float(summary.get('total_tasks'), 0))}, "
                f"pass_rate={safe_float(summary.get('pass_rate')):.2%}, "
                f"p95={safe_float(summary.get('p95_duration_seconds')):.3f}s, "
                f"throughput={safe_float(summary.get('throughput_tps')):.3f} tps"
            )
    else:
        lines.append("- 并发压测: 未启用或未产出结果")
    lines.append(
        f"- Discovery 向量匹配均值: {safe_float(metric_latency.get('Vector_Match', {}).get('mean_seconds')) * 1000.0:.2f}ms"
    )
    lines.append("")
    lines.append("## 5) 安全性测试（负例）")
    if negative_rows:
        for row in negative_rows:
            case_name = str(row.get("negative_case", row.get("case_id", "")))
            status = str(row.get("status", ""))
            error = str(row.get("error", ""))
            level_name = str(row.get("load_level", "")).strip()
            case_label = f"{case_name}[{level_name}]" if level_name else case_name
            lines.append(f"- {case_label}: `{status}`" + (f" | {error}" if error else ""))
    else:
        lines.append("- 未记录负例结果")
    lines.append("")
    lines.append("## 6) MCP 互操作")
    lines.append(f"- 用例通过: {mcp_passed}/{mcp_total} ({(mcp_passed / mcp_total * 100.0) if mcp_total else 0.0:.1f}%)")
    mcp_matrix_rows = [
        row for row in case_assertions if str(row.get("capability_id")) == "mcp_interop.latency_matrix"
    ]
    if mcp_matrix_rows:
        lines.append("- MCP 并发延迟矩阵:")
        for row in mcp_matrix_rows:
            lines.append(
                f"  - {str(row.get('case_id', ''))}: "
                f"`{'passed' if bool(row.get('passed')) else 'failed'}` | {str(row.get('actual', ''))}"
            )
    lines.append("")
    lines.append("## 7) 治理")
    lines.append(f"- 治理动作通过: {governance_passed}/{len(governance_metrics)}")
    lines.append("")
    lines.append("## 8) 成本与规模")
    lines.append(f"- 链上总成本: {total_cost_eth:.8f} ETH = {total_cost_usd:.2f} USD = {total_cost_cny:.2f} CNY")
    if l2_summary_rows:
        for row in sorted(l2_summary_rows, key=lambda item: str(item.get('l2_name', ''))):
            lines.append(
                f"- L2 {row.get('l2_name')}: total={safe_float(row.get('cost_cny')):.2f} CNY, "
                f"avg/tx={safe_float(row.get('avg_cost_cny_per_tx')):.4f} CNY"
            )
    lines.append(f"- 规模外推: {scale_note}")
    lines.append("")
    lines.append("## 9) 图表清单（精简且可解释）")
    for chart_name, desc in chart_desc_map.items():
        if chart_name in chart_files:
            lines.append(f"- `{chart_name}`: {desc}")
    lines.append("")
    lines.append("## 10) 复现命令")
    lines.append("```bash")
    lines.append("python fullflow_tests/run_fullflow.py --account-strategy fresh --governance-mode both --rounds 3")
    lines.append("python fullflow_tests/run_fullflow.py")
    lines.append("```")
    lines.append("")
    lines.append("## 11) 说明")
    lines.append("- 本文为可直接汇报版本；详细原始数据请看 CSV/JSON 产物。")
    lines.append("- 若某图未生成，通常是该类数据在本次 run 中为空。")
    return "\n".join(lines)


def write_reports(
    run_dir: Path,
    phase_metrics: list[dict[str, Any]],
    chain_tx_metrics: list[dict[str, Any]],
    discovery_metrics: list[dict[str, Any]],
    governance_metrics: list[dict[str, Any]],
    case_assertions: list[dict[str, Any]],
    raw_metrics: dict[str, Any],
    usd_per_eth: float,
    reporting_config: dict[str, Any] | None = None,
    mcp_metrics: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg = dict(reporting_config or {})
    usd_cny_rate = safe_float(cfg.get("usd_cny_rate"), 7.20)

    l2_profiles = default_l2_profiles()
    l2_overrides = cfg.get("l2_profiles")
    if isinstance(l2_overrides, dict):
        for name, profile in l2_overrides.items():
            if not isinstance(profile, dict):
                continue
            key = str(name)
            if key not in l2_profiles:
                l2_profiles[key] = {}
            l2_profiles[key]["gas_price_gwei"] = safe_float(profile.get("gas_price_gwei"), l2_profiles[key].get("gas_price_gwei", 0.0))
            l2_profiles[key]["l1_data_fee_eth_per_tx"] = safe_float(profile.get("l1_data_fee_eth_per_tx"), l2_profiles[key].get("l1_data_fee_eth_per_tx", 0.0))

    normalized_case_assertions = normalize_case_assertions(case_assertions)
    enriched_chain_rows = enrich_chain_tx_metrics(chain_tx_metrics=chain_tx_metrics, usd_per_eth=usd_per_eth, usd_cny_rate=usd_cny_rate)
    latency_stats_rows = build_latency_stats_rows(phase_metrics=phase_metrics, discovery_metrics=discovery_metrics, governance_metrics=governance_metrics)

    l2_cost_rows, l2_summary_rows = estimate_l2_costs(
        chain_tx_rows=enriched_chain_rows,
        usd_per_eth=usd_per_eth,
        usd_cny_rate=usd_cny_rate,
        l2_profiles=l2_profiles,
    )
    l2_operation_rows = build_l2_operation_estimate_rows(
        chain_tx_rows=enriched_chain_rows,
        usd_per_eth=usd_per_eth,
        usd_cny_rate=usd_cny_rate,
        l2_profiles=l2_profiles,
        reporting_config=cfg,
    )
    scale_projection_rows = build_scale_projection_rows(
        phase_metrics=phase_metrics,
        discovery_metrics=discovery_metrics,
        governance_metrics=governance_metrics,
        chain_tx_rows=enriched_chain_rows,
        l2_summary_rows=l2_summary_rows,
        usd_per_eth=usd_per_eth,
        usd_cny_rate=usd_cny_rate,
        reporting_config=cfg,
    )
    chain_action_projection_rows = build_chain_action_projection_rows(
        chain_tx_rows=enriched_chain_rows,
        scale_projection_rows=scale_projection_rows,
        usd_per_eth=usd_per_eth,
        usd_cny_rate=usd_cny_rate,
    )

    phase_csv = run_dir / "phase_metrics.csv"
    chain_csv = run_dir / "chain_tx_metrics.csv"
    discovery_csv = run_dir / "discovery_metrics.csv"
    governance_csv = run_dir / "governance_metrics.csv"
    case_csv = run_dir / "case_assertions.csv"
    latency_csv = run_dir / "latency_stats.csv"
    l2_cost_csv = run_dir / "l2_cost_estimates.csv"
    l2_operation_csv = run_dir / "l2_operation_estimates.csv"
    scale_projection_csv = run_dir / "scale_projection.csv"
    chain_action_projection_csv = run_dir / "chain_action_projection.csv"
    raw_json = run_dir / "raw_metrics.json"
    summary_md = run_dir / "summary.md"
    full_report_md = run_dir / "fullflow_report.md"

    phase_fields = sorted({k for row in phase_metrics for k in row.keys()}) if phase_metrics else ["scenario", "status"]
    chain_fields = sorted({k for row in enriched_chain_rows for k in row.keys()}) if enriched_chain_rows else ["category", "status"]
    discovery_fields = sorted({k for row in discovery_metrics for k in row.keys()}) if discovery_metrics else ["metric_type"]
    governance_fields = sorted({k for row in governance_metrics for k in row.keys()}) if governance_metrics else ["mode", "status"]
    case_fields = sorted({k for row in normalized_case_assertions for k in row.keys()}) if normalized_case_assertions else ["phase", "case_id", "capability_id", "passed", "error"]
    latency_fields = sorted({k for row in latency_stats_rows for k in row.keys()}) if latency_stats_rows else ["stat_scope", "latency_stage", "count", "mean_seconds", "p50_seconds", "p95_seconds", "max_seconds"]
    l2_fields = sorted({k for row in l2_cost_rows for k in row.keys()}) if l2_cost_rows else ["l2_name", "scope", "l2_cost_eth", "cost_usd", "cost_cny"]
    l2_op_fields = sorted({k for row in l2_operation_rows for k in row.keys()}) if l2_operation_rows else ["operation_id", "l2_name", "action_count", "gas_used", "per_tx_cost_cny", "total_cost_cny"]
    scale_fields = sorted({k for row in scale_projection_rows for k in row.keys()}) if scale_projection_rows else ["target_agents", "est_total_time_seconds", "est_total_cost_cny"]
    action_projection_fields = sorted({k for row in chain_action_projection_rows for k in row.keys()}) if chain_action_projection_rows else ["target_agents", "tx_type", "projected_count", "est_cost_eth", "est_cost_cny"]

    write_csv(phase_csv, phase_metrics, phase_fields)
    write_csv(chain_csv, enriched_chain_rows, chain_fields)
    write_csv(discovery_csv, discovery_metrics, discovery_fields)
    write_csv(governance_csv, governance_metrics, governance_fields)
    write_csv(case_csv, normalized_case_assertions, case_fields)
    write_csv(latency_csv, latency_stats_rows, latency_fields)
    write_csv(l2_cost_csv, l2_cost_rows, l2_fields)
    write_csv(l2_operation_csv, l2_operation_rows, l2_op_fields)
    write_csv(scale_projection_csv, scale_projection_rows, scale_fields)
    write_csv(chain_action_projection_csv, chain_action_projection_rows, action_projection_fields)

    # MCP 互操作指标
    mcp_rows = list(mcp_metrics or [])
    mcp_csv = run_dir / "mcp_metrics.csv"
    if mcp_rows:
        mcp_fields = sorted({k for row in mcp_rows for k in row.keys()})
        write_csv(mcp_csv, mcp_rows, mcp_fields)

    with raw_json.open("w", encoding="utf-8") as f:
        json.dump(raw_metrics, f, ensure_ascii=False, indent=2)

    run_status = str(raw_metrics.get("status", "")).strip().lower()
    if run_status and run_status != "success":
        chart_files = {}
        summary_text = build_failed_summary_markdown(
            raw_metrics=raw_metrics,
            usd_per_eth=usd_per_eth,
            usd_cny_rate=usd_cny_rate,
            l2_operation_rows=l2_operation_rows,
        )
    else:
        chart_files = generate_charts(
            run_dir=run_dir,
            phase_metrics=phase_metrics,
            latency_stats_rows=latency_stats_rows,
            case_assertions=normalized_case_assertions,
            chain_tx_rows=enriched_chain_rows,
            l2_summary_rows=l2_summary_rows,
            scale_projection_rows=scale_projection_rows,
        )
        # MCP 互操作图表
        mcp_chart_files = generate_mcp_charts(
            run_dir=run_dir,
            case_assertions=normalized_case_assertions,
            mcp_metrics=mcp_rows,
        )
        for cf in mcp_chart_files:
            chart_files[Path(cf).name] = cf
        summary_text = build_summary_markdown(
            phase_metrics=phase_metrics,
            chain_tx_metrics=enriched_chain_rows,
            discovery_metrics=discovery_metrics,
            governance_metrics=governance_metrics,
            case_assertions=normalized_case_assertions,
            latency_stats_rows=latency_stats_rows,
            usd_per_eth=usd_per_eth,
            usd_cny_rate=usd_cny_rate,
            l2_summary_rows=l2_summary_rows,
            l2_operation_rows=l2_operation_rows,
            scale_projection_rows=scale_projection_rows,
            chart_files=chart_files,
            raw_metrics=raw_metrics,
        )
    summary_md.write_text(summary_text, encoding="utf-8")
    full_report_md.write_text(summary_text, encoding="utf-8")

    return {
        "phase_metrics.csv": str(phase_csv),
        "chain_tx_metrics.csv": str(chain_csv),
        "discovery_metrics.csv": str(discovery_csv),
        "governance_metrics.csv": str(governance_csv),
        "case_assertions.csv": str(case_csv),
        "latency_stats.csv": str(latency_csv),
        "l2_cost_estimates.csv": str(l2_cost_csv),
        "l2_operation_estimates.csv": str(l2_operation_csv),
        "scale_projection.csv": str(scale_projection_csv),
        "chain_action_projection.csv": str(chain_action_projection_csv),
        "raw_metrics.json": str(raw_json),
        "summary.md": str(summary_md),
        "fullflow_report.md": str(full_report_md),
        **{name: str(run_dir / name) for name in chart_files.keys()},
    }
