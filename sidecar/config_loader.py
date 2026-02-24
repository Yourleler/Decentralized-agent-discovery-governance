"""
Sidecar 配置加载器。

后续职责：
1. 读取并合并：
   - config/metadata_vectorize_fields.json
   - config/agent_metadata_format.schema.json
   - 环境变量覆盖项
2. 做最小配置校验（路径、阈值、模型名）。
3. 输出强类型配置对象（或字典约束）。
"""

