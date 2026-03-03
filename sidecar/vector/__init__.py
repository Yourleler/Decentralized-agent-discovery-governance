"""
本文件应该做什么：
1. 暴露 sidecar 的向量检索最小公共接口。
2. 统一对外导出 Chroma 索引与 embedding 工厂。
3. 避免业务层直接依赖底层三方库细节。
"""

from .chroma_index import ChromaIndex, ChromaIndexSettings, SearchHit
from .embedding_factory import build_sentence_transformer_embedding

__all__ = [
    "ChromaIndex",
    "ChromaIndexSettings",
    "SearchHit",
    "build_sentence_transformer_embedding",
]
