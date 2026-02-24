"""
向量索引服务。

后续职责：
1. 将融合后的 metadata 转为向量文档。
2. 写入/更新 Chroma 文档与元数据过滤键。
3. 支持按 agentDid 或 capabilityId 删除旧索引。
"""

