
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
        latency_stats_rows: list[dict[str, Any]],
        case_assertions: list[dict[str, Any]],
        chain_tx_rows: list[dict[str, Any]],
        l2_summary_rows: list[dict[str, Any]],
        scale_projection_rows: list[dict[str, Any]],
    ) -> dict[str, str]:
        _ = (
            run_dir,
            latency_stats_rows,
            case_assertions,
            chain_tx_rows,
            l2_summary_rows,
            scale_projection_rows,
        )
        return {}


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
) -> str:
    positive_rows = [row for row in phase_metrics if row.get("scenario") == "positive"]
    passed_rows = [row for row in positive_rows if row.get("status") == "passed"]
    round_rows = [row for row in phase_metrics if row.get("scenario") == "round_summary"]
    tps_values = [safe_float(row.get("round_tps"), 0.0) for row in round_rows if safe_float(row.get("round_tps"), 0.0) > 0]

    total_cost_eth = sum(safe_float(row.get("cost_eth"), 0.0) for row in chain_tx_metrics)
    total_cost_usd = total_cost_eth * usd_per_eth
    total_cost_cny = total_cost_usd * usd_cny_rate

    case_total = len(case_assertions)
    case_passed = sum(1 for row in case_assertions if bool(row.get("passed")))
    capability_count = len({str(row.get("capability_id", "")).strip() for row in case_assertions if str(row.get("capability_id", "")).strip()})

    latency_map = {str(row.get("latency_stage")): row for row in latency_stats_rows if str(row.get("stat_scope")) == "stage"}
    metric_latency_map = {str(row.get("latency_stage")): row for row in latency_stats_rows if str(row.get("stat_scope")) == "metric"}

    discovery_search_rows = [row for row in discovery_metrics if str(row.get("metric_type")) == "search_assertion"]
    discovery_hit_count = sum(1 for row in discovery_search_rows if bool(row.get("found")))
    governance_passed = sum(1 for row in governance_metrics if str(row.get("status")) == "passed")

    vector_rows = [row for row in discovery_metrics if str(row.get("metric_type")) == "vector_index_summary"]
    vector_backend_set = sorted({str(row.get("vector_backend", "")).strip() for row in vector_rows if str(row.get("vector_backend", "")).strip()})
    vector_lines: list[str] = []
    vector_by_op: dict[str, dict[str, Any]] = {}
    for row in vector_rows:
        op = str(row.get("vector_operation", "")).strip()
        if op:
            vector_by_op[op] = row
    for op in ("upsert", "query", "delete"):
        row = vector_by_op.get(op)
        if not row:
            continue
        vector_lines.append(
            f"- 向量{op}: 调用 {int(safe_float(row.get('vector_call_count'), 0))} 次，"
            f"均值 {safe_float(row.get('vector_mean_latency_ms')):.4f}ms，"
            f"最大 {safe_float(row.get('vector_max_latency_ms')):.4f}ms"
        )

    l2_lines: list[str] = []
    for row in sorted(l2_summary_rows, key=lambda item: str(item.get("l2_name", ""))):
        l2_lines.append(
            f"- {row.get('l2_name')}: 总成本约 {safe_float(row.get('cost_cny')):.4f} CNY，"
            f"单笔均值约 {safe_float(row.get('avg_cost_cny_per_tx')):.4f} CNY"
        )
    if not l2_lines:
        l2_lines.append("- 无可用链上交易明细，暂未产出 L2 成本估算。")

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
        l2_op_lines.append("- 暂无按操作类型的 L2 成本估算。")

    scale_line = "- 暂无规模估算数据。"
    if scale_projection_rows:
        peak = max(scale_projection_rows, key=lambda item: int(item.get("target_agents", 0) or 0))
        scale_line = (
            f"- 线性外推（{int(peak.get('target_agents', 0))} agents）："
            f"总时延约 {safe_float(peak.get('est_total_time_seconds')):.2f}s，"
            f"链上总成本约 {safe_float(peak.get('est_total_cost_cny')):.2f} CNY。"
        )

    def _chart_line(filename: str, desc: str) -> str:
        return f"- {desc}: `{chart_files.get(filename, '未生成')}`"

    def _chart_embed(filename: str, alt: str) -> list[str]:
        if filename in chart_files:
            return [f"![{alt}]({filename})", ""]
        return [f"> 图 `{filename}` 未生成（可能缺少数据或绘图库）。", ""]

    lines = [
        "# Fullflow 测试摘要（论文口径）",
        "",
        "## 1. 验证阶段",
        f"- 正向审计总数: {len(positive_rows)}",
        f"- 正向审计通过数: {len(passed_rows)}",
        f"- Auth 平均延迟(T4): {safe_float(latency_map.get('T4_auth', {}).get('mean_seconds')):.4f}s",
        f"- Probe 平均延迟(T8): {safe_float(latency_map.get('T8_probe', {}).get('mean_seconds')):.4f}s",
        f"- Context 平均延迟(T12): {safe_float(latency_map.get('T12_context', {}).get('mean_seconds')):.4f}s",
        f"- 全流程平均延迟(Total): {safe_float(latency_map.get('Total', {}).get('mean_seconds')):.4f}s",
        f"- 平均 TPS(按轮): {mean(tps_values):.4f}",
        "",
        "## 2. 用例覆盖",
        f"- 用例总数: {case_total}",
        f"- 用例通过数: {case_passed}",
        f"- 用例通过率: {(case_passed / case_total * 100.0) if case_total else 0.0:.2f}%",
        f"- 覆盖能力ID数: {capability_count}",
        "",
        "## 3. 发现与向量指标",
        f"- Discovery 检索断言数: {len(discovery_search_rows)}",
        f"- Discovery 命中数: {discovery_hit_count}",
        f"- 向量后端: {', '.join(vector_backend_set) if vector_backend_set else '未记录'}",
        f"- 向量匹配平均耗时: {safe_float(metric_latency_map.get('Vector_Match', {}).get('mean_seconds')) * 1000.0:.3f}ms",
        f"- CID 上传平均耗时: {safe_float(metric_latency_map.get('CID_Upload', {}).get('mean_seconds')) * 1000.0:.3f}ms",
        f"- CID 下载平均耗时: {safe_float(metric_latency_map.get('CID_Download', {}).get('mean_seconds')) * 1000.0:.3f}ms",
    ]
    lines.extend(vector_lines)

    lines.extend(
        [
            "",
            "## 4. 成本阶段",
            f"- 汇率口径: 1 ETH = {usd_per_eth:.2f} USD, 1 USD = {usd_cny_rate:.4f} CNY",
            f"- 链上交易数: {len(chain_tx_metrics)}",
            f"- 总成本(ETH): {total_cost_eth:.8f}",
            f"- 总成本(USD): {total_cost_usd:.4f}",
            f"- 总成本(CNY): {total_cost_cny:.4f}",
            "",
            "## 5. L2 总体估算",
        ]
    )
    lines.extend(l2_lines)

    lines.extend(["", "## 6. L2 按操作估算（含注册/申诉）"])
    lines.extend(l2_op_lines)

    lines.extend(
        [
            "",
            "## 7. 治理阶段",
            f"- 治理动作数: {len(governance_metrics)}",
            f"- 治理通过数: {governance_passed}",
            "",
            "## 8. 大规模估算（线性口径）",
            scale_line,
            "",
            "## 9. 图表",
            _chart_line("chart_latency_stage.png", "阶段延迟图"),
            _chart_line("chart_case_passrate.png", "能力通过率图"),
            _chart_line("chart_tx_cost_eth.png", "链上成本图"),
            _chart_line("chart_l2_cost_cny.png", "L2人民币估算图"),
            _chart_line("chart_scale_projection.png", "规模外推图"),
            "",
        ]
    )

    lines.extend(_chart_embed("chart_latency_stage.png", "阶段延迟统计"))
    lines.extend(_chart_embed("chart_case_passrate.png", "用例通过率"))
    lines.extend(_chart_embed("chart_tx_cost_eth.png", "链上成本"))
    lines.extend(_chart_embed("chart_l2_cost_cny.png", "L2人民币估算"))
    lines.extend(_chart_embed("chart_scale_projection.png", "规模外推"))

    lines.extend(
        [
            "## 10. 说明",
            "- 本摘要聚合自 phase_metrics.csv / chain_tx_metrics.csv / discovery_metrics.csv / governance_metrics.csv / case_assertions.csv / latency_stats.csv / l2_cost_estimates.csv / l2_operation_estimates.csv / scale_projection.csv。",
            "- l2_operation_estimates.csv 在缺失真实交易样本时使用默认 gas 参数估算，可在 reporting.operation_estimation 覆盖。",
            "- CID 指标基于本地 .ipfs_cache 口径，用于稳定复测对比。",
        ]
    )
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
            latency_stats_rows=latency_stats_rows,
            case_assertions=normalized_case_assertions,
            chain_tx_rows=enriched_chain_rows,
            l2_summary_rows=l2_summary_rows,
            scale_projection_rows=scale_projection_rows,
        )
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
        )
    summary_md.write_text(summary_text, encoding="utf-8")

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
        **{name: str(run_dir / name) for name in chart_files.keys()},
    }
