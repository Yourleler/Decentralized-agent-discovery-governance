"""
本文件应该做什么：
1. 提供最小 HTTP 检索接口 `/search`。
2. 输入 query/top_k，返回可用 agent 列表。
3. 仅做协议层，不放业务计算细节。
"""

from __future__ import annotations

from fastapi import FastAPI, Query
from pydantic import BaseModel, Field

from sidecar.wiring import (
    SidecarSettings,
    build_sidecar_container,
    close_sidecar_container,
)


class AdjustLocalScoreRequest(BaseModel):
    """本地评分调整请求。"""

    agent_address: str = Field(..., min_length=1, description="目标 agent 地址")
    alpha_delta: float = Field(0.0, description="本地正向证据增量（可为负）")
    beta_delta: float = Field(0.0, description="本地负向证据增量（可为负）")


def create_app(settings: SidecarSettings | None = None) -> FastAPI:
    """创建最小检索 API 应用。"""
    container = build_sidecar_container(settings)
    app = FastAPI(title="Sidecar Search API", version="0.1.0")

    @app.get("/search")
    def search(
        query: str = Query(..., min_length=1, description="语义检索文本"),
        top_k: int = Query(5, ge=1, le=50, description="返回数量"),
    ) -> dict[str, object]:
        items = container.discovery_service.search_as_dicts(query=query, top_k=top_k)
        return {
            "query": query,
            "top_k": top_k,
            "count": len(items),
            "items": items,
        }

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/local-score/adjust")
    def adjust_local_score(payload: AdjustLocalScoreRequest) -> dict[str, object]:
        updated = container.sync_orchestrator.adjust_local_score(
            agent_address=payload.agent_address,
            alpha_delta=payload.alpha_delta,
            beta_delta=payload.beta_delta,
        )
        return {
            "agent_address": updated.agent_address,
            "alpha": updated.alpha,
            "beta": updated.beta,
            "global_score": updated.global_score,
            "local_score": updated.local_score,
            "confidence_score": updated.confidence_score,
            "final_score": updated.final_score,
            "last_score_update_ts": updated.last_score_update_ts,
        }

    @app.on_event("shutdown")
    def _shutdown() -> None:
        close_sidecar_container(container)

    return app


app = create_app()
