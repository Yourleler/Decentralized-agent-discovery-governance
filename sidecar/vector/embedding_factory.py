"""
本文件应该做什么：
1. 统一创建 embedding 函数，避免业务层重复写三方初始化代码。
2. 默认提供对中文友好的多语模型入口。
3. 保持最小接口，后续可替换为 OpenAI 或其他 embedding 实现。
"""

from __future__ import annotations

from typing import Any


def build_sentence_transformer_embedding(model_name: str = "BAAI/bge-m3") -> Any:
    """创建 sentence-transformers embedding 函数。"""
    try:
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "缺少 sentence-transformers 或 chromadb embedding 组件，请先安装依赖。"
        ) from exc#from exc 是“显式异常因果链”，用于包装异常同时保留原始错误来源

    return SentenceTransformerEmbeddingFunction(model_name=model_name)
