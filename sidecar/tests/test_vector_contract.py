"""
本文件应该做什么：
1. 约束向量检索模块最小契约，确保基础能力可用。
2. 该测试当前仅验证模块可导入与核心类型存在。
3. 后续再按实际检索流程补充集成测试。
"""

from sidecar.vector import ChromaIndexSettings


def test_vector_settings_defaults() -> None:
    settings = ChromaIndexSettings(persist_path="./sidecar/data/chroma")
    assert settings.collection_name == "agent_index"
    assert settings.distance_space == "cosine"
