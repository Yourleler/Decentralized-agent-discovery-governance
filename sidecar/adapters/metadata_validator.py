"""
元数据结构校验器。

后续职责：
1. 使用 config/agent_metadata_format.schema.json 校验结构。
2. 校验关键一致性：
   - metadata.agentDid 与子图 did 一致
   - vcManifest/lazyFetch 字段存在性
3. 产出可诊断的校验错误列表。
"""

