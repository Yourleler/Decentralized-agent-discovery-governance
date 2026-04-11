"""
运行时本地状态封装。

职责：
1. 为 Holder/Verifier 提供统一的 SQLite 交互记录入口。
2. 提供上下文快照哈希、清空历史、申诉证据导出接口。
3. 提供最小化的签发者信任寄存接口（默认 allow_all）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from sidecar.storage.sqlite_state import SQLiteStateStore


DEFAULT_RUNTIME_DB_NAME = "interaction_state.db"
DEFAULT_TRUST_REGISTRY_NAME = "trusted_issuers.json"


class RuntimeStateManager:
    """
    运行时 SQLite 状态管理器。
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.store = SQLiteStateStore(self.db_path)
        self.store.init_db()

    def append_interaction(
        self,
        owner_did: str,
        peer_did: str,
        caller_did: str,
        target_did: str,
        request_data: Any,
        response_data: Any,
        stage: str = "",
        status: str = "unknown",
        latency_ms: int = 0,
        session_id: str = "",
        task_id: str = "",
        source: str = "",
    ) -> int:
        """
        记录一次本地交互。
        """
        return self.store.append_interaction_receipt(
            owner_did=owner_did,
            peer_did=peer_did,
            caller_did=caller_did,
            target_did=target_did,
            request_data=request_data,
            response_data=response_data,
            stage=stage,
            status=status,
            latency_ms=latency_ms,
            session_id=session_id,
            task_id=task_id,
            source=source,
        )

    def get_snapshot_hash(self, owner_did: str, peer_did: str) -> tuple[str, int]:
        """
        读取指定会话视角下的上下文快照哈希。
        """
        return self.store.build_interaction_snapshot_hash(owner_did=owner_did, peer_did=peer_did)

    def reset_peer_history(self, owner_did: str, peer_did: str) -> int:
        """
        清空指定对端的交互历史。
        """
        return self.store.clear_interaction_history(owner_did=owner_did, peer_did=peer_did)

    def export_appeal_payload(self, owner_did: str, peer_did: str, limit: int = 200) -> dict[str, Any]:
        """
        导出申诉证据载荷。
        """
        return self.store.build_appeal_payload(owner_did=owner_did, peer_did=peer_did, limit=limit)

    def close(self) -> None:
        """
        关闭底层 SQLite 连接。
        """
        self.store.close()


class IssuerTrustRegistry:
    """
    最小化签发者信任寄存。

    默认策略：
    - 无配置文件时，允许全部签发者；
    - 配置文件存在时，根据 mode 决定 allow_all 或 whitelist。
    """

    def __init__(self, base_dir: str | Path, file_name: str = DEFAULT_TRUST_REGISTRY_NAME):
        self.base_dir = Path(base_dir)
        self.file_path = self.base_dir / file_name
        self.config = self._load_config()

    def is_trusted(self, issuer_did: str) -> bool:
        """
        判断 issuer 是否受信任。
        """
        mode = str(self.config.get("mode", "allow_all")).strip().lower()
        if mode == "allow_all":
            return True
        trusted = {
            str(item).strip().lower()
            for item in self.config.get("trusted_issuers", [])
            if str(item).strip()
        }
        return str(issuer_did or "").strip().lower() in trusted

    def as_dict(self) -> dict[str, Any]:
        """
        返回当前配置，便于接口层读取。
        """
        return dict(self.config)

    def _load_config(self) -> dict[str, Any]:
        """
        加载配置；缺省返回 allow_all。
        """
        if not self.file_path.exists():
            return {"mode": "allow_all", "trusted_issuers": []}
        try:
            raw = self.file_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                return {"mode": "allow_all", "trusted_issuers": []}
            return {
                "mode": str(data.get("mode", "allow_all")).strip() or "allow_all",
                "trusted_issuers": list(data.get("trusted_issuers", [])),
            }
        except Exception:
            return {"mode": "allow_all", "trusted_issuers": []}


def resolve_runtime_db_path(base_dir: str | Path) -> Path:
    """
    解析运行时 SQLite 路径。

    优先级：
    1. AGENT_RUNTIME_DB_PATH
    2. {base_dir}/interaction_state.db
    """
    env_path = os.getenv("AGENT_RUNTIME_DB_PATH")
    if env_path:
        return Path(env_path).resolve()
    return (Path(base_dir) / DEFAULT_RUNTIME_DB_NAME).resolve()

