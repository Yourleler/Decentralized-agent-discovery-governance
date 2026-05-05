"""
轻量请求权限与签名绑定工具。
设计目标：
1. 不引入完整 OAuth/GNAP。
2. 参考 RFC 9396 / RFC 8707 / RFC 9421 / RFC 9449 的核心思想。
3. 在现有 DID 签名流程上补齐“资源 + 动作 + 防重放 + 权限细节”绑定。
"""

from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any


DEFAULT_CLOCK_SKEW_SECONDS = 300


@dataclass(slots=True)
class PolicyDecision:
    """权限判定结果。"""

    allowed: bool
    reason: str
    matched_tool: dict[str, Any] | None = None


def _canonical_json(data: Any) -> str:
    """把对象转成稳定 JSON 字符串，便于签名和哈希。"""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_hex(text: str) -> str:
    """计算 UTF-8 文本的 SHA-256 十六进制摘要。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_authorization_details(
    detail_type: str,
    actions: list[str] | None = None,
    locations: list[str] | None = None,
    datatypes: list[str] | None = None,
    identifier: str = "",
    privileges: list[str] | None = None,
) -> dict[str, Any]:
    """
    构造最小 authorizationDetails。
    字段结构参考 RFC 9396，但只保留毕设需要的最小子集。
    """
    return {
        "type": str(detail_type or "").strip(),
        "actions": [str(item).strip() for item in (actions or []) if str(item).strip()],
        "locations": [str(item).strip() for item in (locations or []) if str(item).strip()],
        "datatypes": [str(item).strip() for item in (datatypes or []) if str(item).strip()],
        "identifier": str(identifier or "").strip(),
        "privileges": [str(item).strip() for item in (privileges or []) if str(item).strip()],
    }


def compute_authorization_details_hash(details: Any) -> str:
    """对 authorizationDetails 做稳定哈希。"""
    if details in (None, "", [], {}):
        return ""
    return _sha256_hex(_canonical_json(details))


def _normalize_timestamp(raw_value: Any) -> tuple[int | None, str]:
    """
    把时间字段统一转成 Unix 秒。
    兼容 Unix 秒/毫秒与 ISO 8601 字符串。
    """
    if raw_value in (None, ""):
        return None, "缺少 timestamp"

    if isinstance(raw_value, (int, float)):
        value = float(raw_value)
        if value > 10_000_000_000:
            value = value / 1000.0
        return int(value), ""

    text = str(raw_value).strip()
    if not text:
        return None, "timestamp 为空"

    try:
        if text.endswith("Z"):
            text = text.replace("Z", "+00:00")
        dt = _dt.datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return int(dt.timestamp()), ""
    except ValueError:
        return None, "timestamp 格式非法"


def build_request_signature_payload(
    request_data: dict[str, Any],
    http_method: str = "POST",
    target_uri: str = "",
) -> str:
    """
    构造请求签名基字符串。
    参考 RFC 9421 / RFC 9449，但保持最小实现。
    """
    body = copy.deepcopy(request_data)
    for field_name in ("signature", "senderSignature", "verifier_signature", "request_signature"):
        body.pop(field_name, None)

    auth_hash = compute_authorization_details_hash(body.get("authorizationDetails"))
    body_hash = _sha256_hex(_canonical_json(body))
    payload = {
        "method": str(http_method or "POST").upper(),
        "targetUri": str(target_uri or "").strip(),
        "requestId": str(body.get("requestId") or "").strip(),
        "timestamp": body.get("timestamp"),
        "nonce": str(body.get("nonce") or "").strip(),
        "resource": str(body.get("resource") or "").strip(),
        "action": str(body.get("action") or "").strip(),
        "authorizationDetailsHash": auth_hash,
        "bodyHash": body_hash,
    }
    return _canonical_json(payload)


def validate_request_envelope(
    request_data: dict[str, Any],
    expected_resource: str = "",
    allowed_actions: list[str] | None = None,
    clock_skew_seconds: int = DEFAULT_CLOCK_SKEW_SECONDS,
) -> tuple[bool, str]:
    """
    校验请求基础字段。
    这里只检查字段合法性，不负责 DID 验签。
    """
    request_id = str(request_data.get("requestId") or "").strip()
    if not request_id:
        return False, "缺少 requestId"

    _, ts_error = _normalize_timestamp(request_data.get("timestamp"))
    if ts_error:
        return False, ts_error

    req_ts, _ = _normalize_timestamp(request_data.get("timestamp"))
    now_ts = int(time.time())
    assert req_ts is not None
    if abs(now_ts - req_ts) > int(clock_skew_seconds):
        return False, "timestamp 超出允许时间窗口"

    resource = str(request_data.get("resource") or "").strip()
    if not resource:
        return False, "缺少 resource"
    if expected_resource and resource != expected_resource:
        return False, f"resource 不匹配: expected={expected_resource}, got={resource}"

    action = str(request_data.get("action") or "").strip()
    if not action:
        return False, "缺少 action"
    if allowed_actions is not None and action not in allowed_actions:
        return False, f"action 不允许: {action}"

    auth_details = request_data.get("authorizationDetails")
    if auth_details not in (None, "", [], {}):
        if not isinstance(auth_details, dict):
            return False, "authorizationDetails 必须为对象"
        detail_type = str(auth_details.get("type") or "").strip()
        if not detail_type:
            return False, "authorizationDetails.type 不能为空"

    return True, "ok"


def with_request_envelope(
    payload: dict[str, Any],
    *,
    resource: str,
    action: str,
    nonce: str | None = None,
    request_id: str | None = None,
    timestamp: str | float | int | None = None,
    authorization_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """为请求补齐统一互操作字段。"""
    result = copy.deepcopy(payload)
    result["resource"] = str(resource or "").strip()
    result["action"] = str(action or "").strip()
    result["requestId"] = str(request_id or uuid.uuid4())
    result["nonce"] = str(nonce or uuid.uuid4())
    result["timestamp"] = timestamp if timestamp is not None else _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if authorization_details not in (None, "", [], {}):
        result["authorizationDetails"] = authorization_details
    return result


def _match_resource_pattern(requested_resource: str, allowed_resources: list[str]) -> bool:
    """
    资源匹配规则：
    - `*` 表示全匹配
    - `prefix*` 表示前缀匹配
    - 其他按全等匹配
    """
    requested = str(requested_resource or "").strip()
    if not allowed_resources:
        return True

    for item in allowed_resources:
        pattern = str(item or "").strip()
        if not pattern:
            continue
        if pattern == "*":
            return True
        if pattern.endswith("*") and requested.startswith(pattern[:-1]):
            return True
        if requested == pattern:
            return True
    return False


def _extract_tool_manifest(vc: dict[str, Any]) -> list[dict[str, Any]]:
    """从 AgentToolsetCredential 中提取工具清单。"""
    vc_types = vc.get("type", [])
    if isinstance(vc_types, str):
        vc_types = [vc_types]
    if "AgentToolsetCredential" not in vc_types:
        return []

    subject = vc.get("credentialSubject")
    if not isinstance(subject, dict):
        return []
    tool_manifest = subject.get("toolManifest")
    if not isinstance(tool_manifest, list):
        return []
    return [item for item in tool_manifest if isinstance(item, dict)]


def evaluate_tool_authorization(
    *,
    tool_identifier: str,
    action: str,
    resource: str,
    vcs: list[dict[str, Any]] | None,
) -> PolicyDecision:
    """
    根据持有的 AgentToolsetCredential 判断工具调用是否允许。
    """
    requested_tool = str(tool_identifier or "").strip()
    requested_action = str(action or "").strip()
    requested_resource = str(resource or "").strip()
    if not requested_tool:
        return PolicyDecision(False, "缺少 tool identifier")

    for vc in vcs or []:
        for tool_item in _extract_tool_manifest(vc):
            identifier = str(tool_item.get("identifier") or "").strip()
            if identifier != requested_tool:
                continue

            allowed_actions = [
                str(item).strip()
                for item in tool_item.get("allowedActions", [])
                if str(item).strip()
            ]
            if allowed_actions and requested_action not in allowed_actions:
                return PolicyDecision(
                    False,
                    f"工具动作未授权: tool={requested_tool} action={requested_action}",
                    matched_tool=tool_item,
                )

            allowed_resources = [
                str(item).strip()
                for item in tool_item.get("allowedResources", [])
                if str(item).strip()
            ]
            if not _match_resource_pattern(requested_resource, allowed_resources):
                return PolicyDecision(
                    False,
                    f"工具资源未授权: tool={requested_tool} resource={requested_resource}",
                    matched_tool=tool_item,
                )

            return PolicyDecision(True, "ok", matched_tool=tool_item)

    return PolicyDecision(False, f"未找到已授权工具: {requested_tool}")
