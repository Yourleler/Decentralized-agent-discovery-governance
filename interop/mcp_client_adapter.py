"""
轻量 MCP 外部工具调用适配器。
说明：
1. 支持最小 HTTP JSON-RPC 风格封装。
2. 支持官方常见 stdio 本地 MCP Server。
3. stdio 路径实现最小 initialize / initialized / tools / resources。
4. 对于少数旧版参考 Server 不支持 initialize 的情况，提供轻量兼容回退。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import requests


DEFAULT_PROTOCOL_VERSION = "2025-11-25"


class MCPClientError(RuntimeError):
    """MCP 调用异常。"""


def _is_method_not_found_error(exc: Exception) -> bool:
    """判断是否属于 MCP method not found。"""
    text = str(exc or "").lower()
    return "method not found" in text or "-32601" in text


@dataclass(slots=True)
class MCPServerConfig:
    """MCP Server 最小配置。"""

    server_id: str
    transport: str
    endpoint: str = ""
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    timeout_seconds: float = 15.0
    headers: dict[str, str] | None = None
    protocol_version: str = DEFAULT_PROTOCOL_VERSION


class MCPBaseClient:
    """MCP 客户端最小公共接口。"""

    def list_tools(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def list_resources(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def read_resource(self, uri: str) -> dict[str, Any]:
        raise NotImplementedError

    def close(self) -> None:
        """关闭底层连接。HTTP 客户端可为空实现。"""


class MCPHttpClient(MCPBaseClient):
    """轻量 MCP HTTP 客户端。"""

    def __init__(
        self,
        endpoint: str,
        timeout_seconds: float = 15.0,
        session: requests.Session | None = None,
        headers: dict[str, str] | None = None,
        protocol_version: str = DEFAULT_PROTOCOL_VERSION,
    ) -> None:
        self.endpoint = str(endpoint or "").strip()
        if not self.endpoint:
            raise ValueError("MCP endpoint 不能为空")
        self.timeout_seconds = float(timeout_seconds)
        self.session = session or requests.Session()
        self.headers = dict(headers or {})
        self.protocol_version = str(protocol_version or DEFAULT_PROTOCOL_VERSION).strip()

    def _invoke(self, method: str, params: dict[str, Any] | None = None) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": str(method or "").strip(),
            "params": params or {},
        }
        request_headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": self.protocol_version,
            **self.headers,
        }
        try:
            response = self.session.post(
                self.endpoint,
                json=payload,
                timeout=self.timeout_seconds,
                headers=request_headers,
            )
        except requests.RequestException as exc:
            raise MCPClientError(f"MCP 请求失败: {exc}") from exc

        if response.status_code >= 400:
            raise MCPClientError(
                f"MCP HTTP 错误: status={response.status_code}, body={response.text[:300]}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise MCPClientError(f"MCP 返回非 JSON: {response.text[:300]}") from exc

        if not isinstance(data, dict):
            raise MCPClientError("MCP 返回结构非法")
        if data.get("error"):
            raise MCPClientError(f"MCP 业务错误: {data['error']}")
        return data.get("result")

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._invoke("tools/list")
        if not isinstance(result, dict):
            return []
        tools = result.get("tools")
        if not isinstance(tools, list):
            return []
        return [item for item in tools if isinstance(item, dict)]

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self._invoke(
            "tools/call",
            {
                "name": str(name or "").strip(),
                "arguments": arguments or {},
            },
        )
        if not isinstance(result, dict):
            return {"content": result}
        return result

    def list_resources(self) -> list[dict[str, Any]]:
        try:
            result = self._invoke("resources/list")
        except MCPClientError as exc:
            if _is_method_not_found_error(exc):
                return []
            raise
        if not isinstance(result, dict):
            return []
        resources = result.get("resources")
        if not isinstance(resources, list):
            return []
        return [item for item in resources if isinstance(item, dict)]

    def read_resource(self, uri: str) -> dict[str, Any]:
        try:
            result = self._invoke(
                "resources/read",
                {
                    "uri": str(uri or "").strip(),
                },
            )
        except MCPClientError as exc:
            if _is_method_not_found_error(exc):
                return {}
            raise
        if not isinstance(result, dict):
            return {"content": result}
        return result

    def close(self) -> None:
        self.session.close()


class MCPStdioClient(MCPBaseClient):
    """最小 stdio MCP 客户端。"""

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        timeout_seconds: float = 15.0,
        env: dict[str, str] | None = None,
        protocol_version: str = DEFAULT_PROTOCOL_VERSION,
    ) -> None:
        self.command = str(command or "").strip()
        if not self.command:
            raise ValueError("stdio MCP command 不能为空")
        self.args = [str(item) for item in (args or [])]
        self.timeout_seconds = float(timeout_seconds)
        self.env = {str(k): str(v) for k, v in dict(env or {}).items()}
        self.protocol_version = str(protocol_version or DEFAULT_PROTOCOL_VERSION).strip()
        self._message_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr_lines: list[str] = []
        self._protocol_errors: list[str] = []
        self._closed = False

        self.process = self._launch_process()
        self._stdout_thread = threading.Thread(target=self._stdout_loop, daemon=True)
        self._stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

        self._legacy_mode = False
        self._initialize_session()

    def _launch_process(self) -> subprocess.Popen[str]:
        popen_env = os.environ.copy()
        popen_env.update(self.env)
        try:
            return subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=popen_env,
            )
        except OSError as exc:
            raise MCPClientError(f"无法启动 MCP stdio Server: {exc}") from exc

    def _stdout_loop(self) -> None:
        if self.process.stdout is None:
            return
        for raw_line in self.process.stdout:
            line = str(raw_line or "").strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                self._protocol_errors.append(f"stdout 非法 JSON: {line[:200]}")
                continue
            if isinstance(message, dict):
                self._message_queue.put(message)

    def _stderr_loop(self) -> None:
        if self.process.stderr is None:
            return
        for raw_line in self.process.stderr:
            line = str(raw_line or "").strip()
            if not line:
                continue
            self._stderr_lines.append(line)
            if len(self._stderr_lines) > 50:
                self._stderr_lines = self._stderr_lines[-50:]

    def _ensure_running(self) -> None:
        if self._closed:
            raise MCPClientError("MCP stdio 客户端已关闭")
        if self.process.poll() is not None:
            raise MCPClientError(self._build_process_error("MCP stdio Server 已退出"))

    def _build_process_error(self, prefix: str) -> str:
        stderr_tail = " | ".join(self._stderr_lines[-3:]) if self._stderr_lines else ""
        protocol_tail = " | ".join(self._protocol_errors[-3:]) if self._protocol_errors else ""
        details = [item for item in (stderr_tail, protocol_tail) if item]
        if not details:
            return prefix
        return f"{prefix}: {' | '.join(details)}"

    def _send(self, payload: dict[str, Any]) -> None:
        self._ensure_running()
        if self.process.stdin is None:
            raise MCPClientError("MCP stdio Server 无可写 stdin")
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        try:
            self.process.stdin.write(serialized + "\n")
            self.process.stdin.flush()
        except OSError as exc:
            raise MCPClientError(self._build_process_error(f"写入 MCP stdio Server 失败: {exc}")) from exc

    def _wait_for_response(self, request_id: str, timeout_seconds: float | None = None) -> dict[str, Any]:
        deadline = time.monotonic() + float(timeout_seconds or self.timeout_seconds)
        while time.monotonic() < deadline:
            self._ensure_running()
            remaining = max(0.05, deadline - time.monotonic())
            try:
                message = self._message_queue.get(timeout=remaining)
            except queue.Empty:
                continue

            if not isinstance(message, dict):
                continue

            if "result" not in message and "error" not in message and "method" in message:
                if "id" in message:
                    self._send(
                        {
                            "jsonrpc": "2.0",
                            "id": message.get("id"),
                            "error": {
                                "code": -32601,
                                "message": "轻量客户端不支持服务端主动请求",
                            },
                        }
                    )
                continue

            if str(message.get("id")) == str(request_id):
                return message

        raise MCPClientError(self._build_process_error("等待 MCP 响应超时"))

    def _initialize_session(self) -> None:
        init_id = str(uuid.uuid4())
        self._send(
            {
                "jsonrpc": "2.0",
                "id": init_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "dagg-mcp-client",
                        "version": "0.1.0",
                    },
                },
            }
        )

        response = self._wait_for_response(init_id)
        error_obj = response.get("error")
        if isinstance(error_obj, dict):
            code = int(error_obj.get("code", 0) or 0)
            message = str(error_obj.get("message") or "").lower()
            if code in (-32601, -32602) or "initialize" in message or "method not found" in message:
                self._legacy_mode = True
                return
            raise MCPClientError(f"MCP initialize 失败: {error_obj}")

        self._send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }
        )

    def _invoke(self, method: str, params: dict[str, Any] | None = None) -> Any:
        request_id = str(uuid.uuid4())
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": str(method or "").strip(),
                "params": params or {},
            }
        )
        response = self._wait_for_response(request_id)
        if response.get("error"):
            raise MCPClientError(f"MCP 业务错误: {response['error']}")
        return response.get("result")

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._invoke("tools/list")
        if not isinstance(result, dict):
            return []
        tools = result.get("tools")
        if not isinstance(tools, list):
            return []
        return [item for item in tools if isinstance(item, dict)]

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self._invoke(
            "tools/call",
            {
                "name": str(name or "").strip(),
                "arguments": arguments or {},
            },
        )
        if not isinstance(result, dict):
            return {"content": result}
        return result

    def list_resources(self) -> list[dict[str, Any]]:
        try:
            result = self._invoke("resources/list")
        except MCPClientError as exc:
            if _is_method_not_found_error(exc):
                return []
            raise
        if not isinstance(result, dict):
            return []
        resources = result.get("resources")
        if not isinstance(resources, list):
            return []
        return [item for item in resources if isinstance(item, dict)]

    def read_resource(self, uri: str) -> dict[str, Any]:
        try:
            result = self._invoke(
                "resources/read",
                {
                    "uri": str(uri or "").strip(),
                },
            )
        except MCPClientError as exc:
            if _is_method_not_found_error(exc):
                return {}
            raise
        if not isinstance(result, dict):
            return {"content": result}
        return result

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        try:
            if self.process.stdin:
                self.process.stdin.close()
        except OSError:
            pass

        try:
            self.process.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1.0)

        for stream_name in ("stdout", "stderr"):
            stream = getattr(self.process, stream_name, None)
            try:
                if stream:
                    stream.close()
            except OSError:
                pass

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


class MCPServerRegistry:
    """MCP Server 配置注册表。"""

    def __init__(self, items: dict[str, MCPServerConfig] | None = None) -> None:
        self._items = dict(items or {})

    @classmethod
    def from_dict(cls, raw_data: dict[str, Any] | None) -> "MCPServerRegistry":
        items = {}
        for server_id, config in (raw_data or {}).items():
            if not isinstance(config, dict):
                continue

            endpoint = str(config.get("endpoint") or "").strip()
            command = str(config.get("command") or "").strip()
            transport = str(config.get("transport") or "").strip().lower()
            if not transport:
                if endpoint:
                    transport = "http"
                elif command:
                    transport = "stdio"

            if transport not in {"http", "stdio"}:
                continue
            if transport == "http" and not endpoint:
                continue
            if transport == "stdio" and not command:
                continue

            items[str(server_id).strip()] = MCPServerConfig(
                server_id=str(server_id).strip(),
                transport=transport,
                endpoint=endpoint,
                command=command,
                args=[str(item) for item in list(config.get("args") or [])],
                env={str(k): str(v) for k, v in dict(config.get("env") or {}).items()},
                timeout_seconds=float(config.get("timeout_seconds", 15.0)),
                headers=dict(config.get("headers") or {}),
                protocol_version=str(config.get("protocol_version") or DEFAULT_PROTOCOL_VERSION).strip(),
            )
        return cls(items=items)

    def get(self, server_id: str) -> MCPServerConfig | None:
        return self._items.get(str(server_id or "").strip())

    def create_client(self, server_id: str) -> MCPBaseClient:
        config = self.get(server_id)
        if config is None:
            raise MCPClientError(f"未找到 MCP Server 配置: {server_id}")

        if config.transport == "stdio":
            return MCPStdioClient(
                command=config.command,
                args=config.args,
                timeout_seconds=config.timeout_seconds,
                env=config.env,
                protocol_version=config.protocol_version,
            )

        return MCPHttpClient(
            endpoint=config.endpoint,
            timeout_seconds=config.timeout_seconds,
            headers=config.headers,
            protocol_version=config.protocol_version,
        )

    def as_dict(self) -> dict[str, Any]:
        result = {}
        for server_id, config in self._items.items():
            result[server_id] = {
                "transport": config.transport,
                "endpoint": config.endpoint,
                "command": config.command,
                "args": list(config.args),
                "env": dict(config.env or {}),
                "timeout_seconds": config.timeout_seconds,
                "headers": dict(config.headers or {}),
                "protocol_version": config.protocol_version,
            }
        return result
