"""
向量文本构造器。

后续职责：
1. 按 config/metadata_vectorize_fields.json 的模板拼接文本。
2. 支持 global 文本 + per-capability 文本两层结构。
3. 输出 document_id、text、metadata 三元组供向量库写入。
"""

