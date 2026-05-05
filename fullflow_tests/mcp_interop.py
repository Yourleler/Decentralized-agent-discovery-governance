"""
MCP 互操作专项测试模块。

用途：在 fullflow 测试流程中，在 Verification 和 Governance 之间
执行 MCP 互操作能力的全面验证，包括连接性、工具发现、工具调用、
权限控制正例/负例以及延迟统计。

产出：
  - case_assertions: 每条用例的通过/失败断言
  - mcp_metrics: 延迟统计等指标行
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

# 确保项目根路径
_current_dir = Path(__file__).resolve().parent
_project_root = _current_dir.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from interop.mcp_client_adapter import (
    MCPClientError,
    MCPServerRegistry,
    MCPStdioClient,
)
from interop.request_policy import (
    build_authorization_details,
    evaluate_tool_authorization,
    with_request_envelope,
)


def _make_assertion(
    case_id: str,
    capability_id: str,
    phase: str,
    passed: bool,
    expected: str,
    actual: str,
    error: str = "",
) -> dict[str, Any]:
    """构造标准化断言记录。"""
    return {
        "case_id": case_id,
        "capability_id": capability_id,
        "phase": phase,
        "passed": passed,
        "expected": expected,
        "actual": actual,
        "error": error,
    }


def _make_metric(
    case_id: str,
    metric_name: str,
    value: float,
    unit: str = "seconds",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造标准化指标记录。"""
    row = {
        "case_id": case_id,
        "metric_name": metric_name,
        "value": value,
        "unit": unit,
        "phase": "mcp_interop",
    }
    if extra:
        row.update(extra)
    return row


def _percentile(values: list[float], q: float) -> float:
    """计算分位值（线性插值）。"""
    if not values:
        return 0.0
    ordered = sorted(float(item) for item in values)
    if q <= 0:
        return ordered[0]
    if q >= 1:
        return ordered[-1]
    pos = (len(ordered) - 1) * q
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    if low == high:
        return ordered[low]
    weight = pos - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _load_mcp_registry(config: dict[str, Any]) -> MCPServerRegistry:
    """根据测试配置加载 MCP Server 注册表。"""
    mcp_cfg = config.get("mcp_interop", {})
    config_path = mcp_cfg.get("servers_config_path", "config/mcp_servers.json")
    abs_path = Path(config_path).resolve()
    if not abs_path.exists():
        abs_path = _project_root / config_path
    with abs_path.open("r", encoding="utf-8") as f:
        raw_data = json.load(f)
    return MCPServerRegistry.from_dict(raw_data)


# ============================================================
# 测试用例
# ============================================================

def test_stdio_connect(registry: MCPServerRegistry, server_id: str) -> dict[str, Any]:
    """
    用例：MCP stdio 连接性测试。
    验证能否启动 stdio MCP Server 并完成 initialize 握手。
    """
    case_id = f"mcp_stdio_connect_{server_id}"
    t0 = time.monotonic()
    try:
        client = registry.create_client(server_id)
        elapsed = time.monotonic() - t0
        client.close()
        return _make_assertion(
            case_id=case_id,
            capability_id="mcp_interop.stdio_connect",
            phase="mcp_interop",
            passed=True,
            expected="initialize 成功",
            actual=f"连接成功，耗时 {elapsed:.3f}s",
        )
    except Exception as exc:
        return _make_assertion(
            case_id=case_id,
            capability_id="mcp_interop.stdio_connect",
            phase="mcp_interop",
            passed=False,
            expected="initialize 成功",
            actual="",
            error=str(exc),
        )


def test_tools_discovery(registry: MCPServerRegistry, server_id: str, expected_tools: list[str]) -> dict[str, Any]:
    """
    用例：MCP 工具发现测试。
    验证 tools/list 返回的工具名称列表是否包含预期工具。
    """
    case_id = f"mcp_tools_discovery_{server_id}"
    try:
        client = registry.create_client(server_id)
        try:
            tools = client.list_tools()
            tool_names = [t.get("name", "") for t in tools]
            found = [name for name in expected_tools if name in tool_names]
            all_found = len(found) == len(expected_tools)
            return _make_assertion(
                case_id=case_id,
                capability_id="mcp_interop.tools_discovery",
                phase="mcp_interop",
                passed=all_found,
                expected=f"发现工具: {expected_tools}",
                actual=f"实际工具: {tool_names}",
                error="" if all_found else f"缺失: {set(expected_tools) - set(tool_names)}",
            )
        finally:
            client.close()
    except Exception as exc:
        return _make_assertion(
            case_id=case_id,
            capability_id="mcp_interop.tools_discovery",
            phase="mcp_interop",
            passed=False,
            expected=f"发现工具: {expected_tools}",
            actual="",
            error=str(exc),
        )


def test_tool_call(
    registry: MCPServerRegistry,
    server_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    validate_fn: Any = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """
    用例：MCP 工具调用测试。
    验证能否成功调用指定工具并获得有效返回。
    """
    case_id = f"mcp_tool_call_{server_id}_{tool_name.replace('/', '_')}"
    try:
        client = registry.create_client(server_id)
        try:
            t0 = time.monotonic()
            result = client.call_tool(tool_name, arguments)
            elapsed = time.monotonic() - t0

            # 提取文本内容
            content_text = ""
            if isinstance(result, dict):
                content_list = result.get("content", [])
                if isinstance(content_list, list):
                    for item in content_list:
                        if isinstance(item, dict) and item.get("type") == "text":
                            content_text += str(item.get("text", ""))

            passed = True
            actual_desc = f"返回 {len(content_text)} 字符，耗时 {elapsed:.3f}s"
            error_msg = ""

            if validate_fn and callable(validate_fn):
                try:
                    validate_fn(content_text)
                except AssertionError as ve:
                    passed = False
                    error_msg = str(ve)

            metric = _make_metric(case_id, f"tool_call_{tool_name}", elapsed, extra={"server_id": server_id, "tool_name": tool_name})

            return _make_assertion(
                case_id=case_id,
                capability_id="mcp_interop.tool_call",
                phase="mcp_interop",
                passed=passed,
                expected=f"{tool_name} 返回有效内容",
                actual=actual_desc,
                error=error_msg,
            ), metric
        finally:
            client.close()
    except Exception as exc:
        return _make_assertion(
            case_id=case_id,
            capability_id="mcp_interop.tool_call",
            phase="mcp_interop",
            passed=False,
            expected=f"{tool_name} 返回有效内容",
            actual="",
            error=str(exc),
        ), None


def test_resources_graceful(registry: MCPServerRegistry, server_id: str) -> dict[str, Any]:
    """
    用例：resources/list 兼容性测试。
    验证对不支持 resources/list 的 Server 能否优雅降级返回空列表。
    """
    case_id = f"mcp_resources_graceful_{server_id}"
    try:
        client = registry.create_client(server_id)
        try:
            resources = client.list_resources()
            return _make_assertion(
                case_id=case_id,
                capability_id="mcp_interop.resources_graceful",
                phase="mcp_interop",
                passed=isinstance(resources, list),
                expected="返回 list（可为空）",
                actual=f"返回 {type(resources).__name__}，长度 {len(resources) if isinstance(resources, list) else 'N/A'}",
            )
        finally:
            client.close()
    except Exception as exc:
        return _make_assertion(
            case_id=case_id,
            capability_id="mcp_interop.resources_graceful",
            phase="mcp_interop",
            passed=False,
            expected="返回 list（可为空）",
            actual="",
            error=str(exc),
        )


def test_vc_auth_positive() -> dict[str, Any]:
    """
    用例：Toolset VC 权限控制正例。
    携带有效 VC + 合法 action 调用工具，应当被允许。
    """
    case_id = "mcp_vc_auth_positive"
    toolset_vc = {
        "type": ["VerifiableCredential", "AgentToolsetCredential"],
        "credentialSubject": {
            "id": "did:ethr:sepolia:0xholder",
            "toolManifest": [
                {
                    "name": "Time Query via MCP",
                    "identifier": "get_current_time",
                    "providerProtocol": "mcp",
                    "serverId": "official-time",
                    "allowedActions": ["query"],
                    "allowedResources": ["resource:time:*"],
                    "permissions": "external-read",
                    "riskLevel": "low",
                    "operationalStatus": "active",
                },
                {
                    "name": "Web Fetch via MCP",
                    "identifier": "fetch",
                    "providerProtocol": "mcp",
                    "serverId": "official-fetch",
                    "allowedActions": ["query"],
                    "allowedResources": ["resource:web:*"],
                    "permissions": "external-read",
                    "riskLevel": "medium",
                    "operationalStatus": "active",
                },
            ],
        },
    }
    decision = evaluate_tool_authorization(
        tool_identifier="get_current_time",
        action="query",
        resource="resource:time:current",
        vcs=[toolset_vc],
    )
    return _make_assertion(
        case_id=case_id,
        capability_id="mcp_interop.vc_auth_positive",
        phase="mcp_interop",
        passed=decision.allowed,
        expected="allowed=True",
        actual=f"allowed={decision.allowed}, reason={decision.reason}",
    )


def test_vc_auth_negative_action() -> dict[str, Any]:
    """
    用例：Toolset VC 权限控制负例（非法 action）。
    携带有效 VC 但使用未授权的 action（如 execute），应当被拒绝。
    """
    case_id = "mcp_vc_auth_negative_action"
    toolset_vc = {
        "type": ["VerifiableCredential", "AgentToolsetCredential"],
        "credentialSubject": {
            "id": "did:ethr:sepolia:0xholder",
            "toolManifest": [
                {
                    "name": "Time Query via MCP",
                    "identifier": "get_current_time",
                    "providerProtocol": "mcp",
                    "serverId": "official-time",
                    "allowedActions": ["query"],
                    "allowedResources": ["resource:time:*"],
                },
            ],
        },
    }
    decision = evaluate_tool_authorization(
        tool_identifier="get_current_time",
        action="execute",  # 非法 action
        resource="resource:time:current",
        vcs=[toolset_vc],
    )
    return _make_assertion(
        case_id=case_id,
        capability_id="mcp_interop.vc_auth_negative",
        phase="mcp_interop",
        passed=not decision.allowed,
        expected="allowed=False（非法 action 应被拒绝）",
        actual=f"allowed={decision.allowed}, reason={decision.reason}",
    )


def test_vc_auth_negative_no_vc() -> dict[str, Any]:
    """
    用例：Toolset VC 权限控制负例（无 VC）。
    不携带任何 VC 直接请求工具调用，应当被拒绝。
    """
    case_id = "mcp_vc_auth_negative_no_vc"
    decision = evaluate_tool_authorization(
        tool_identifier="get_current_time",
        action="query",
        resource="resource:time:current",
        vcs=[],  # 无 VC
    )
    return _make_assertion(
        case_id=case_id,
        capability_id="mcp_interop.vc_auth_negative",
        phase="mcp_interop",
        passed=not decision.allowed,
        expected="allowed=False（无 VC 应被拒绝）",
        actual=f"allowed={decision.allowed}, reason={decision.reason}",
    )


def test_latency_batch(
    registry: MCPServerRegistry,
    server_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    call_count: int = 20,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    用例：MCP 工具调用延迟批量统计。
    连续调用指定次数，统计 avg/P50/P95/max 延迟分布。
    """
    case_id = f"mcp_latency_batch_{server_id}"
    latencies: list[float] = []
    errors: list[str] = []

    try:
        client = registry.create_client(server_id)
        try:
            for i in range(call_count):
                t0 = time.monotonic()
                try:
                    client.call_tool(tool_name, arguments)
                    elapsed = time.monotonic() - t0
                    latencies.append(elapsed)
                except Exception as exc:
                    elapsed = time.monotonic() - t0
                    latencies.append(elapsed)
                    errors.append(f"call_{i}: {exc}")
        finally:
            client.close()
    except Exception as exc:
        return _make_assertion(
            case_id=case_id,
            capability_id="mcp_interop.latency_batch",
            phase="mcp_interop",
            passed=False,
            expected=f"{call_count} 次调用延迟统计",
            actual="",
            error=str(exc),
        ), []

    if not latencies:
        return _make_assertion(
            case_id=case_id,
            capability_id="mcp_interop.latency_batch",
            phase="mcp_interop",
            passed=False,
            expected=f"{call_count} 次调用延迟统计",
            actual="无有效数据",
            error="",
        ), []

    sorted_lat = sorted(latencies)
    n = len(sorted_lat)
    avg_val = sum(sorted_lat) / n
    p50_val = sorted_lat[int(n * 0.5)]
    p95_val = sorted_lat[int(n * 0.95)] if n > 1 else sorted_lat[-1]
    max_val = sorted_lat[-1]
    min_val = sorted_lat[0]

    metrics = [
        _make_metric(case_id, "MCP_ToolCall_Avg", avg_val, extra={"server_id": server_id, "stat_scope": "batch"}),
        _make_metric(case_id, "MCP_ToolCall_P50", p50_val, extra={"server_id": server_id, "stat_scope": "batch"}),
        _make_metric(case_id, "MCP_ToolCall_P95", p95_val, extra={"server_id": server_id, "stat_scope": "batch"}),
        _make_metric(case_id, "MCP_ToolCall_Max", max_val, extra={"server_id": server_id, "stat_scope": "batch"}),
        _make_metric(case_id, "MCP_ToolCall_Min", min_val, extra={"server_id": server_id, "stat_scope": "batch"}),
    ]

    actual_desc = (
        f"完成 {n}/{call_count} 次调用，"
        f"avg={avg_val:.4f}s p50={p50_val:.4f}s p95={p95_val:.4f}s max={max_val:.4f}s"
    )
    error_desc = f"{len(errors)} 次失败" if errors else ""

    assertion = _make_assertion(
        case_id=case_id,
        capability_id="mcp_interop.latency_batch",
        phase="mcp_interop",
        passed=len(errors) == 0,
        expected=f"{call_count} 次调用延迟统计",
        actual=actual_desc,
        error=error_desc,
    )
    return assertion, metrics


def _call_tool_worker(
    registry: MCPServerRegistry,
    server_id: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> tuple[list[float], list[str]]:
    """单个并发 worker 内复用一个 client 连续调用，返回耗时数组与错误数组。"""
    call_count = max(1, int(arguments.pop("__call_count__", 1)))
    call_args = dict(arguments)
    latencies: list[float] = []
    errors: list[str] = []
    try:
        client = registry.create_client(server_id)
    except Exception as exc:
        return latencies, [str(exc)] * call_count
    try:
        for _ in range(call_count):
            started = time.monotonic()
            try:
                client.call_tool(tool_name, call_args)
            except Exception as exc:
                errors.append(str(exc))
            finally:
                latencies.append(time.monotonic() - started)
    except Exception as exc:
        errors.append(str(exc))
    finally:
        try:
            client.close()
        except Exception:
            pass
    return latencies, errors


def test_latency_matrix(
    registry: MCPServerRegistry,
    server_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    levels: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    用例：MCP 并发延迟矩阵统计。
    按档位执行并发请求，输出各档位 avg/p50/p95/max 与通过率。
    """
    assertions: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []

    for idx, level in enumerate(levels):
        level_name = str(level.get("name", f"L{idx + 1}")).strip() or f"L{idx + 1}"
        concurrency = max(1, int(level.get("concurrency", 1)))
        total_calls = max(concurrency, int(level.get("total_calls", concurrency)))
        case_id = f"mcp_latency_matrix_{server_id}_{level_name}"

        latencies: list[float] = []
        errors: list[str] = []
        worker_count = min(concurrency, total_calls)
        base_calls = total_calls // worker_count
        remainder = total_calls % worker_count
        calls_per_worker = [
            base_calls + (1 if idx < remainder else 0)
            for idx in range(worker_count)
        ]
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = []
            for call_count in calls_per_worker:
                worker_args = dict(arguments)
                worker_args["__call_count__"] = int(call_count)
                futures.append(
                    pool.submit(
                        _call_tool_worker,
                        registry=registry,
                        server_id=server_id,
                        tool_name=tool_name,
                        arguments=worker_args,
                    )
                )
            for future in as_completed(futures):
                worker_lat, worker_err = future.result()
                latencies.extend([float(item) for item in worker_lat])
                errors.extend([str(item) for item in worker_err if str(item)])

        avg_val = (sum(latencies) / len(latencies)) if latencies else 0.0
        p50_val = _percentile(latencies, 0.50)
        p95_val = _percentile(latencies, 0.95)
        max_val = max(latencies) if latencies else 0.0
        pass_rate = (float(total_calls - len(errors)) / float(total_calls)) if total_calls > 0 else 0.0

        metrics.extend(
            [
                _make_metric(
                    case_id,
                    "MCP_Matrix_Avg",
                    avg_val,
                    extra={
                        "server_id": server_id,
                        "stat_scope": "matrix_summary",
                        "load_level": level_name,
                        "concurrency": concurrency,
                        "total_calls": total_calls,
                        "pass_rate": pass_rate,
                    },
                ),
                _make_metric(
                    case_id,
                    "MCP_Matrix_P50",
                    p50_val,
                    extra={
                        "server_id": server_id,
                        "stat_scope": "matrix_summary",
                        "load_level": level_name,
                        "concurrency": concurrency,
                        "total_calls": total_calls,
                        "pass_rate": pass_rate,
                    },
                ),
                _make_metric(
                    case_id,
                    "MCP_Matrix_P95",
                    p95_val,
                    extra={
                        "server_id": server_id,
                        "stat_scope": "matrix_summary",
                        "load_level": level_name,
                        "concurrency": concurrency,
                        "total_calls": total_calls,
                        "pass_rate": pass_rate,
                    },
                ),
                _make_metric(
                    case_id,
                    "MCP_Matrix_Max",
                    max_val,
                    extra={
                        "server_id": server_id,
                        "stat_scope": "matrix_summary",
                        "load_level": level_name,
                        "concurrency": concurrency,
                        "total_calls": total_calls,
                        "pass_rate": pass_rate,
                    },
                ),
            ]
        )
        assertions.append(
            _make_assertion(
                case_id=case_id,
                capability_id="mcp_interop.latency_matrix",
                phase="mcp_interop",
                passed=len(errors) == 0,
                expected=f"[{level_name}] 并发={concurrency}, 调用={total_calls} 全部成功",
                actual=(
                    f"pass_rate={pass_rate:.4f}, avg={avg_val:.4f}s, "
                    f"p95={p95_val:.4f}s, max={max_val:.4f}s"
                ),
                error=f"{len(errors)} 次失败" if errors else "",
            )
        )

    return assertions, metrics


# ============================================================
# 主入口
# ============================================================

def run_mcp_interop_flow(
    config: dict[str, Any],
    run_dir: Path,
) -> dict[str, Any]:
    """
    执行 MCP 互操作全部测试用例。

    参数：
      config: fullflow 运行配置
      run_dir: 当前运行的输出目录

    返回：
      dict 包含 case_assertions, mcp_metrics, status
    """
    mcp_cfg = config.get("mcp_interop", {})
    if not mcp_cfg.get("enabled", True):
        print("[MCP_INTEROP] 已禁用，跳过")
        return {"case_assertions": [], "mcp_metrics": [], "status": "skipped"}

    batch_count = int(mcp_cfg.get("batch_call_count", 20))
    test_servers = mcp_cfg.get("test_servers", ["official-time"])
    matrix_cfg = dict(mcp_cfg.get("latency_matrix", {}))
    matrix_enabled = bool(matrix_cfg.get("enabled", True))
    raw_levels = matrix_cfg.get("levels", [])
    matrix_levels: list[dict[str, Any]] = []
    if isinstance(raw_levels, list):
        for idx, item in enumerate(raw_levels):
            if not isinstance(item, dict):
                continue
            matrix_levels.append(
                {
                    "name": str(item.get("name", f"L{idx + 1}")).strip() or f"L{idx + 1}",
                    "concurrency": max(1, int(item.get("concurrency", 1))),
                    "total_calls": max(1, int(item.get("total_calls", 20))),
                }
            )
    if not matrix_levels:
        matrix_levels = [
            {"name": "S", "concurrency": 2, "total_calls": 20},
            {"name": "M", "concurrency": 8, "total_calls": 60},
            {"name": "L", "concurrency": 16, "total_calls": 120},
        ]

    case_assertions: list[dict[str, Any]] = []
    mcp_metrics: list[dict[str, Any]] = []

    try:
        registry = _load_mcp_registry(config)
    except Exception as exc:
        print(f"[MCP_INTEROP] 加载 MCP 配置失败: {exc}")
        case_assertions.append(_make_assertion(
            case_id="mcp_config_load",
            capability_id="mcp_interop.config",
            phase="mcp_interop",
            passed=False,
            expected="MCP 配置加载成功",
            actual="",
            error=str(exc),
        ))
        return {"case_assertions": case_assertions, "mcp_metrics": mcp_metrics, "status": "failed"}

    # 每个 MCP Server 的预期工具映射
    server_tool_map = {
        "official-time": {
            "expected_tools": ["get_current_time", "convert_time"],
            "test_tool": "get_current_time",
            "test_args": {"timezone": "Asia/Shanghai"},
            "validate": lambda text: None,  # 时间工具只要有返回就行
        },
        "official-fetch": {
            "expected_tools": ["fetch"],
            "test_tool": "fetch",
            "test_args": {"url": "https://example.com"},
            "validate": lambda text: (
                None if len(text) >= 50
                else (_ for _ in ()).throw(AssertionError(f"返回内容过短: {len(text)} 字符"))
            ),
        },
    }

    for server_id in test_servers:
        tool_info = server_tool_map.get(server_id)
        if not tool_info:
            print(f"[MCP_INTEROP] 未知 server_id: {server_id}，跳过")
            continue

        print(f"[MCP_INTEROP] 测试 Server: {server_id}")

        # 1. 连接性测试
        assertion = test_stdio_connect(registry, server_id)
        case_assertions.append(assertion)
        print(f"  stdio_connect: {'PASS' if assertion['passed'] else 'FAIL'}")

        if not assertion["passed"]:
            print(f"  跳过 {server_id} 后续测试（连接失败）")
            continue

        # 2. 工具发现测试
        assertion = test_tools_discovery(registry, server_id, tool_info["expected_tools"])
        case_assertions.append(assertion)
        print(f"  tools_discovery: {'PASS' if assertion['passed'] else 'FAIL'}")

        # 3. 工具调用测试
        assertion, metric = test_tool_call(
            registry, server_id,
            tool_info["test_tool"],
            tool_info["test_args"],
            tool_info.get("validate"),
        )
        case_assertions.append(assertion)
        if metric:
            mcp_metrics.append(metric)
        print(f"  tool_call: {'PASS' if assertion['passed'] else 'FAIL'}")

        # 4. resources 兼容性测试
        assertion = test_resources_graceful(registry, server_id)
        case_assertions.append(assertion)
        print(f"  resources_graceful: {'PASS' if assertion['passed'] else 'FAIL'}")

    # 5. 权限控制测试（不依赖真实 Server）
    print("[MCP_INTEROP] 权限控制测试")

    assertion = test_vc_auth_positive()
    case_assertions.append(assertion)
    print(f"  vc_auth_positive: {'PASS' if assertion['passed'] else 'FAIL'}")

    assertion = test_vc_auth_negative_action()
    case_assertions.append(assertion)
    print(f"  vc_auth_negative_action: {'PASS' if assertion['passed'] else 'FAIL'}")

    assertion = test_vc_auth_negative_no_vc()
    case_assertions.append(assertion)
    print(f"  vc_auth_negative_no_vc: {'PASS' if assertion['passed'] else 'FAIL'}")

    # 6. 延迟批量统计（仅对第一个可用 Server）
    primary_server = test_servers[0] if test_servers else None
    if primary_server and primary_server in server_tool_map:
        tool_info = server_tool_map[primary_server]
        print(f"[MCP_INTEROP] 延迟批量统计: {primary_server} x {batch_count} 次")
        assertion, batch_metrics = test_latency_batch(
            registry, primary_server,
            tool_info["test_tool"],
            tool_info["test_args"],
            call_count=batch_count,
        )
        case_assertions.append(assertion)
        mcp_metrics.extend(batch_metrics)
        print(f"  latency_batch: {'PASS' if assertion['passed'] else 'FAIL'}")

        if matrix_enabled:
            print(f"[MCP_INTEROP] 并发延迟矩阵: {primary_server} levels={len(matrix_levels)}")
            matrix_assertions, matrix_metrics = test_latency_matrix(
                registry=registry,
                server_id=primary_server,
                tool_name=tool_info["test_tool"],
                arguments=tool_info["test_args"],
                levels=matrix_levels,
            )
            case_assertions.extend(matrix_assertions)
            mcp_metrics.extend(matrix_metrics)
            matrix_passed = sum(1 for item in matrix_assertions if bool(item.get("passed")))
            print(f"  latency_matrix: {matrix_passed}/{len(matrix_assertions)} PASS")

    # 统计
    total = len(case_assertions)
    passed = sum(1 for a in case_assertions if a.get("passed"))
    print(f"[MCP_INTEROP] 完成: {passed}/{total} 通过")

    return {
        "case_assertions": case_assertions,
        "mcp_metrics": mcp_metrics,
        "status": "success" if passed == total else "partial",
    }
