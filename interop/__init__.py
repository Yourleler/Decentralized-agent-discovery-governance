"""
轻量互操作层导出。

本目录用于承载本项目的最小 A2A / MCP 兼容封装，
尽量不侵入现有“发现-认证-治理”主流程。
"""

from .a2a_gateway import A2AGatewayService, create_a2a_blueprint
from .mcp_client_adapter import (
    MCPBaseClient,
    MCPClientError,
    MCPHttpClient,
    MCPServerRegistry,
    MCPStdioClient,
)
from .profile_adapter import InteropProfile, build_agent_card, build_interop_profile
from .request_policy import (
    PolicyDecision,
    build_authorization_details,
    build_request_signature_payload,
    compute_authorization_details_hash,
    evaluate_tool_authorization,
    validate_request_envelope,
)

__all__ = [
    "A2AGatewayService",
    "create_a2a_blueprint",
    "MCPBaseClient",
    "MCPClientError",
    "MCPHttpClient",
    "MCPStdioClient",
    "MCPServerRegistry",
    "InteropProfile",
    "build_agent_card",
    "build_interop_profile",
    "PolicyDecision",
    "build_authorization_details",
    "build_request_signature_payload",
    "compute_authorization_details_hash",
    "evaluate_tool_authorization",
    "validate_request_envelope",
]
