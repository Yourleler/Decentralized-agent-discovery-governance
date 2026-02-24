"""
IPFS 元数据获取适配器。

后续职责：
1. 按 CID 下载 metadata（优先复用 infrastructure/ipfs.py 能力）。
2. 做基本完整性校验与 JSON 解析。
3. 返回 metadata 及必要校验信息（hash/source/timestamp）。
"""

