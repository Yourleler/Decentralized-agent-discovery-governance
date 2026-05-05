"""
metadata -> 互操作描述适配器。
目标：
1. 不改现有发现主流程。
2. 把 metadata 中稳定字段整理成 A2A 可理解的对外说明。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class InteropProfile:
    """最小互操作描述。"""

    agent_did: str
    service_name: str
    service_summary: str
    endpoint: str
    supported_protocols: list[str]
    supported_interaction_modes: list[str]
    auth_mode: str
    a2a_endpoint: str
    vc_types: list[str]
    capabilities: list[dict[str, Any]]


def _pick_primary_endpoint(service_obj: dict[str, Any]) -> str:
    """从 metadata.service 中提取主要入口地址。"""
    for field_name in ("endpoint", "probeUrl", "healthUrl", "url"):
        value = service_obj.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()

    endpoints = service_obj.get("endpoints")
    if isinstance(endpoints, list):
        for item in endpoints:
            if isinstance(item, dict):
                url = str(item.get("url") or "").strip()
                if url:
                    return url

    service_endpoint = service_obj.get("serviceEndpoint")
    if isinstance(service_endpoint, dict):
        url = str(service_endpoint.get("url") or "").strip()
        if url:
            return url
    return ""


def build_interop_profile(metadata: dict[str, Any]) -> InteropProfile:
    """从 metadata 构建统一互操作描述。"""
    if not isinstance(metadata, dict):
        raise ValueError("metadata 必须为对象")

    service_obj = metadata.get("service")
    if not isinstance(service_obj, dict):
        raise ValueError("metadata.service 缺失或类型非法")

    interop_obj = metadata.get("interop")
    if not isinstance(interop_obj, dict):
        interop_obj = {}

    endpoint = _pick_primary_endpoint(service_obj)
    supported_protocols = [
        str(item).strip()
        for item in interop_obj.get("supportedProtocols", ["native"])
        if str(item).strip()
    ]
    supported_modes = [
        str(item).strip()
        for item in interop_obj.get("supportedInteractionModes", service_obj.get("interactionModes", []))
        if str(item).strip()
    ]
    auth_mode = str(interop_obj.get("authMode") or "did-sig").strip()
    a2a_endpoint = str(interop_obj.get("a2aEndpoint") or "").strip()
    if not a2a_endpoint and endpoint:
        a2a_endpoint = endpoint.rstrip("/") + "/a2a"

    vc_manifest = metadata.get("vcManifest")
    vc_types = []
    if isinstance(vc_manifest, dict):
        vc_types = [str(item).strip() for item in vc_manifest.get("types", []) if str(item).strip()]

    capabilities = metadata.get("capabilities")
    if not isinstance(capabilities, list):
        capabilities = []

    return InteropProfile(
        agent_did=str(metadata.get("agentDid") or "").strip(),
        service_name=str(service_obj.get("name") or "").strip(),
        service_summary=str(service_obj.get("summary") or "").strip(),
        endpoint=endpoint,
        supported_protocols=supported_protocols,
        supported_interaction_modes=supported_modes,
        auth_mode=auth_mode,
        a2a_endpoint=a2a_endpoint,
        vc_types=vc_types,
        capabilities=[item for item in capabilities if isinstance(item, dict)],
    )


def build_agent_card(profile: InteropProfile) -> dict[str, Any]:
    """构建最小 Agent Card。"""
    skills = []
    for capability in profile.capabilities:
        skills.append(
            {
                "id": str(capability.get("id") or "").strip(),
                "name": str(capability.get("name") or "").strip(),
                "description": str(capability.get("description") or "").strip(),
                "tags": [str(item).strip() for item in capability.get("inputs", []) if str(item).strip()],
            }
        )

    return {
        "protocolVersion": "0.2.5",
        "name": profile.service_name,
        "description": profile.service_summary,
        "url": profile.a2a_endpoint or profile.endpoint,
        "preferredTransport": "JSON_RPC",
        "authentication": {
            "mode": profile.auth_mode,
        },
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
        },
        "skills": skills,
        "extensions": {
            "agentDid": profile.agent_did,
            "supportedProtocols": profile.supported_protocols,
            "supportedInteractionModes": profile.supported_interaction_modes,
            "vcTypes": profile.vc_types,
        },
    }
