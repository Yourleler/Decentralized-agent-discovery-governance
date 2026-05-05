"""
测试专用的最小 stdio MCP Server。
用途：
1. 验证 initialize / initialized 正常流程。
2. 验证旧版不支持 initialize 的兼容回退。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def write_message(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def build_tools_result() -> dict[str, Any]:
    return {
        "tools": [
            {
                "name": "tool.time.now",
                "title": "Current Time",
                "description": "Return a fake current time for tests.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "timezone": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            }
        ]
    }


def build_resources_result() -> dict[str, Any]:
    return {
        "resources": [
            {
                "uri": "resource://time/info",
                "name": "time-info",
                "mimeType": "application/json",
            }
        ]
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-no-init", action="store_true")
    args = parser.parse_args()

    for raw_line in sys.stdin:
        line = str(raw_line or "").strip()
        if not line:
            continue

        try:
            message = json.loads(line)
        except ValueError:
            continue

        method = message.get("method")
        request_id = message.get("id")

        if method == "initialize":
            if args.legacy_no_init:
                write_message(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32601,
                            "message": "Method not found: initialize",
                        },
                    }
                )
                continue

            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {
                            "tools": {},
                            "resources": {},
                        },
                        "serverInfo": {
                            "name": "fake-stdio-server",
                            "version": "1.0.0",
                        },
                    },
                }
            )
            continue

        if method == "notifications/initialized":
            continue

        if method == "tools/list":
            write_message({"jsonrpc": "2.0", "id": request_id, "result": build_tools_result()})
            continue

        if method == "tools/call":
            params = message.get("params") or {}
            arguments = params.get("arguments") or {}
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": f"fake-time::{arguments.get('timezone', 'UTC')}",
                            }
                        ],
                        "isError": False,
                    },
                }
            )
            continue

        if method == "resources/list":
            write_message({"jsonrpc": "2.0", "id": request_id, "result": build_resources_result()})
            continue

        if method == "resources/read":
            params = message.get("params") or {}
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "contents": [
                            {
                                "uri": params.get("uri", "resource://unknown"),
                                "mimeType": "application/json",
                                "text": "{\"timezone\":\"UTC\"}",
                            }
                        ]
                    },
                }
            )
            continue

        if request_id is not None:
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {method}",
                    },
                }
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
