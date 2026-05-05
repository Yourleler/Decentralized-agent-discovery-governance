"""
轻量 A2A Gateway。
首期目标：
1. 暴露最小 Agent Card。
2. 支持 message/send。
3. 支持 tasks/get。
4. 内部可桥接 MCP 工具调用。
"""

from __future__ import annotations

import datetime as _dt
import traceback
import uuid
from typing import Any, Callable

from flask import Blueprint, jsonify, request

from .mcp_client_adapter import MCPClientError, MCPServerRegistry
from .profile_adapter import InteropProfile, build_agent_card
from .request_policy import (
    build_request_signature_payload,
    evaluate_tool_authorization,
    validate_request_envelope,
)


TaskExecutor = Callable[[dict[str, Any]], dict[str, Any]]


class A2AGatewayService:
    """A2A 网关服务封装。"""

    def __init__(
        self,
        *,
        validator: Any,
        runtime_state: Any,
        holder_did: str,
        profile: InteropProfile,
        vcs_getter: Callable[[], list[dict[str, Any]]],
        task_executor: TaskExecutor | None = None,
        mcp_registry: MCPServerRegistry | None = None,
    ) -> None:
        self.validator = validator
        self.runtime_state = runtime_state
        self.holder_did = str(holder_did or "").strip()
        self.profile = profile
        self.vcs_getter = vcs_getter
        self.task_executor = task_executor
        self.mcp_registry = mcp_registry
        self._tasks: dict[str, dict[str, Any]] = {}

    def get_agent_card(self) -> dict[str, Any]:
        return build_agent_card(self.profile)

    def list_tasks(self) -> dict[str, dict[str, Any]]:
        return dict(self._tasks)

    def _record_task(self, task_id: str, task_data: dict[str, Any]) -> None:
        self._tasks[str(task_id)] = task_data

    def _record_interaction(self, *, sender_did: str, request_data: dict[str, Any], response_data: dict[str, Any], stage: str, status: str, task_id: str) -> None:
        self.runtime_state.append_interaction(
            owner_did=self.holder_did,
            peer_did=sender_did,
            caller_did=sender_did,
            target_did=self.holder_did,
            request_data=request_data,
            response_data=response_data,
            stage=stage,
            status=status,
            task_id=task_id,
            source="a2a_gateway",
        )

    def handle_message(self, payload: dict[str, Any], *, target_uri: str) -> tuple[dict[str, Any], int]:
        sender_did = str(payload.get("senderDid") or payload.get("verifier_did") or "").strip()
        signature = str(
            payload.get("senderSignature")
            or payload.get("signature")
            or payload.get("verifier_signature")
            or ""
        ).strip()

        if not sender_did or not signature:
            return {"error": "缺少 senderDid 或 signature"}, 400

        ok, msg = validate_request_envelope(payload)
        if not ok:
            return {"error": msg}, 400

        signature_payload = build_request_signature_payload(payload, http_method="POST", target_uri=target_uri)
        valid_sig, sig_msg = self.validator.verify_request_signature(signature_payload, signature, sender_did)
        if not valid_sig:
            return {"error": f"签名校验失败: {sig_msg}"}, 401

        task_id = str(payload.get("taskId") or payload.get("requestId") or uuid.uuid4())
        now_iso = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._record_task(task_id, {"taskId": task_id, "status": "running", "updatedAt": now_iso})

        try:
            tool_call = payload.get("toolCall")
            if isinstance(tool_call, dict):
                result = self._handle_tool_call(payload=payload, tool_call=tool_call, task_id=task_id)
            else:
                result = self._handle_native_task(payload=payload)

            task_payload = {
                "taskId": task_id,
                "status": "completed",
                "updatedAt": now_iso,
                "result": result,
            }
            self._record_task(task_id, task_payload)
            self._record_interaction(
                sender_did=sender_did,
                request_data=payload,
                response_data=task_payload,
                stage="a2a",
                status="success",
                task_id=task_id,
            )
            return task_payload, 200
        except PermissionError as exc:
            error_payload = {
                "taskId": task_id,
                "status": "failed",
                "updatedAt": now_iso,
                "error": str(exc),
            }
            self._record_task(task_id, error_payload)
            self._record_interaction(
                sender_did=sender_did,
                request_data=payload,
                response_data=error_payload,
                stage="a2a",
                status="denied",
                task_id=task_id,
            )
            return error_payload, 403
        except Exception as exc:  # noqa: BLE001
            error_payload = {
                "taskId": task_id,
                "status": "failed",
                "updatedAt": now_iso,
                "error": str(exc),
            }
            self._record_task(task_id, error_payload)
            self._record_interaction(
                sender_did=sender_did,
                request_data=payload,
                response_data=error_payload,
                stage="a2a",
                status="failed",
                task_id=task_id,
            )
            traceback.print_exc()
            return error_payload, 500

    def _handle_native_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.task_executor is None:
            return {
                "mode": "native",
                "message": "未配置 task executor，已完成最小 A2A 接入验证",
                "echo": payload.get("message"),
            }
        return self.task_executor(payload)

    def _handle_tool_call(self, payload: dict[str, Any], tool_call: dict[str, Any], task_id: str) -> dict[str, Any]:
        provider_protocol = str(tool_call.get("providerProtocol") or "").strip().lower()
        tool_name = str(tool_call.get("toolName") or tool_call.get("name") or "").strip()
        server_id = str(tool_call.get("serverId") or "").strip()
        resource = str(payload.get("resource") or "").strip()
        action = str(payload.get("action") or "").strip()
        sender_did = str(payload.get("senderDid") or payload.get("verifier_did") or "").strip()

        decision = evaluate_tool_authorization(
            tool_identifier=tool_name,
            action=action,
            resource=resource,
            vcs=self.vcs_getter(),
        )
        if not decision.allowed:
            denied_payload = {
                "tool": tool_name,
                "providerProtocol": provider_protocol or "native",
                "serverId": server_id,
                "status": "denied",
                "reason": decision.reason,
            }
            self._record_interaction(
                sender_did=sender_did,
                request_data=payload,
                response_data=denied_payload,
                stage="mcp_tool",
                status="denied",
                task_id=task_id,
            )
            raise PermissionError(decision.reason)

        if provider_protocol != "mcp":
            native_payload = {
                "mode": provider_protocol or "native",
                "message": "非 MCP 工具调用，已通过权限校验",
                "tool": tool_name,
            }
            self._record_interaction(
                sender_did=sender_did,
                request_data=payload,
                response_data=native_payload,
                stage="mcp_tool",
                status="success",
                task_id=task_id,
            )
            return native_payload

        if self.mcp_registry is None:
            raise MCPClientError("未配置 MCP Server 注册表")

        client = self.mcp_registry.create_client(server_id)
        try:
            arguments = tool_call.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            tool_result = client.call_tool(tool_name, arguments=arguments)
            result_payload = {
                "mode": "mcp",
                "serverId": server_id,
                "tool": tool_name,
                "result": tool_result,
            }
            self._record_interaction(
                sender_did=sender_did,
                request_data=payload,
                response_data=result_payload,
                stage="mcp_tool",
                status="success",
                task_id=task_id,
            )
            return result_payload
        finally:
            close_fn = getattr(client, "close", None)
            if callable(close_fn):
                close_fn()


def create_a2a_blueprint(service: A2AGatewayService) -> Blueprint:
    """创建可直接挂载到 Flask 的 Blueprint。"""
    blueprint = Blueprint("a2a_gateway", __name__)

    @blueprint.get("/.well-known/agent-card.json")
    def well_known_agent_card() -> Any:
        return jsonify(service.get_agent_card())

    @blueprint.get("/a2a/agent-card")
    def agent_card() -> Any:
        return jsonify(service.get_agent_card())

    @blueprint.post("/a2a/message/send")
    def message_send() -> Any:
        payload = request.get_json(silent=True) or {}
        result, status_code = service.handle_message(payload, target_uri=request.base_url)
        return jsonify(result), status_code

    @blueprint.get("/a2a/tasks/<task_id>")
    def get_task(task_id: str) -> Any:
        item = service.list_tasks().get(str(task_id))
        if item is None:
            return jsonify({"error": "task 不存在"}), 404
        return jsonify(item)

    return blueprint
