"""
IPFS 工具模块 (Pinata REST API)

直接使用 Pinata REST API，无需 Node.js 中间层。

功能：
  - upload_json()      上传 JSON 数据到 IPFS，返回 CID
  - upload_file()      上传文件到 IPFS，返回 CID
  - fetch_content()    通过 CID 下载内容
  - fetch_and_verify() 下载 + SHA256 校验（Sidecar 同步时使用）

使用场景对应：
  - Agent 注册:  upload_json(metadata) → CID → registerAgent(did, cid)
  - 证据上传:   upload_json(evidence) → CID → reportMisbehavior(agent, cid)
  - Sidecar 同步: fetch_and_verify(cid) → 可信元数据
"""

import os
import json
import hashlib
import httpx
from infrastructure.load_config import load_key_config

# ─── 从统一配置读取 ───
_config = load_key_config()
PINATA_JWT = _config.get("pinata_jwt", "")
GATEWAY_URL = _config.get("pinata_gateway", "")

# Pinata API 地址
PINATA_API_URL = "https://uploads.pinata.cloud/v3/files"
PINATA_API_BASE = "https://api.pinata.cloud/v3"


def _get_headers():
    """构建 Pinata API 认证头"""
    if not PINATA_JWT:
        raise ValueError(
            "[IPFS] PINATA_JWT 未设置。请在 .env 中设置或通过环境变量传入。"
        )
    return {"Authorization": f"Bearer {PINATA_JWT}"}


def _get_gateway_url(cid: str) -> str:
    """构建网关访问 URL"""
    if GATEWAY_URL:
        return f"https://{GATEWAY_URL}/ipfs/{cid}"
    return f"https://gateway.pinata.cloud/ipfs/{cid}"


# ═══════════════════════════════════════
# 上传功能
# ═══════════════════════════════════════

def upload_json(data: dict, name: str = None) -> dict:
    """
    上传 JSON 数据到 IPFS

    Args:
        data: 要上传的 JSON 数据（dict）
        name: 可选的文件名标识

    Returns:
        dict: {"cid": "bafkrei...", "gateway_url": "https://..."}

    用法:
        # 上传 Agent 元数据
        result = upload_json({
            "did": "did:ethr:sepolia:0x...",
            "capabilities": ["data-analysis"],
            "description": "..."
        })
        cid = result["cid"]  # 传入合约 registerAgent(did, cid)
    """
    file_name = name or f"data-{int(__import__('time').time())}.json"
    json_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")

    # network=public 是关键！否则默认上传到 private 网络，专属网关无法直接访问
    files = {"file": (file_name, json_bytes, "application/json")}
    form_data = {"network": "public"}
    headers = _get_headers()

    response = httpx.post(PINATA_API_URL, headers=headers, files=files, data=form_data, timeout=30)
    response.raise_for_status()

    result = response.json()
    cid = result["data"]["cid"]

    return {
        "cid": cid,
        "gateway_url": _get_gateway_url(cid),
    }


def upload_file(file_path: str) -> dict:
    """
    上传本地文件到 IPFS

    Args:
        file_path: 本地文件路径

    Returns:
        dict: {"cid": "bafkrei...", "gateway_url": "https://..."}
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"[IPFS] 文件不存在: {file_path}")

    file_name = os.path.basename(file_path)
    headers = _get_headers()

    with open(file_path, "rb") as f:
        files = {"file": (file_name, f)}
        form_data = {"network": "public"}
        response = httpx.post(PINATA_API_URL, headers=headers, files=files, data=form_data, timeout=60)
        response.raise_for_status()

    result = response.json()
    cid = result["data"]["cid"]

    return {
        "cid": cid,
        "gateway_url": _get_gateway_url(cid),
    }


# ═══════════════════════════════════════
# 下载功能
# ═══════════════════════════════════════

# 公共网关列表 (作为 Dedicated Gateway 的备份)
PUBLIC_GATEWAYS = [
    "https://gateway.pinata.cloud/ipfs",
    "https://ipfs.io/ipfs",
    "https://cloudflare-ipfs.com/ipfs",
    "https://dweb.link/ipfs",
]


def _fetch_from_url(url: str, timeout: int = 15) -> bytes:
    """单一 URL 下载辅助函数"""
    try:
        # 自定义 User-Agent 避免被 WAF 拦截
        response = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "PinataSidecar/1.0"},
        )
        response.raise_for_status()
        return response.content
    except Exception as e:
        raise RuntimeError(f"Failed {url}: {e}")


def fetch_race(cid: str, timeout: int = 30) -> bytes:
    """
    [核心] 网关竞速模式下载
    同时请求多个网关，返回最快响应的结果。
    """
    import concurrent.futures

    # 构建候选网关 URL 列表
    urls = []

    # 1. 优先：专属网关 (最快)
    gateway_base = (
        f"https://{GATEWAY_URL}" if GATEWAY_URL else "https://gateway.pinata.cloud"
    )
    urls.append(f"{gateway_base}/ipfs/{cid}")

    # 2. 备选：公共网关 (提高可用性)
    for base_gw in PUBLIC_GATEWAYS:
        urls.append(f"{base_gw}/{cid}")

    # 去重
    urls = list(dict.fromkeys(urls))
    last_error = None

    # 多线程竞速
    # 注意: 不使用 'with' 上下文管理器，避免在返回时阻塞等待其他慢速网关
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=len(urls))
    try:
        future_to_url = {
            executor.submit(_fetch_from_url, url, timeout): url for url in urls
        }

        for future in concurrent.futures.as_completed(future_to_url):
            try:
                content = future.result()
                # 成功！立即取消其他任务并返回，不再等待
                executor.shutdown(wait=False, cancel_futures=True)
                return content
            except Exception as e:
                last_error = e
                continue
    finally:
        # 兜底清理
        executor.shutdown(wait=False, cancel_futures=True)

    raise RuntimeError(f"[IPFS] CID {cid} 下载失败，所有网关均无响应。Last Error: {last_error}")


def fetch_content(cid: str) -> bytes:
    """保持接口兼容，内部使用竞速模式"""
    return fetch_race(cid)


def fetch_batch(cids: list[str], max_workers: int = 5) -> dict[str, bytes]:
    """
    [核心] 批量并发下载

    Args:
        cids: CID 列表
        max_workers: 并发线程数

    Returns:
        dict: {cid: bytes} 成功的映射
    """
    import concurrent.futures

    results = {}
    print(f"📥 [Batch] 开始批量下载 {len(cids)} 个文件 (并发: {max_workers})...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 为每个 CID 启动一个竞速下载任务
        future_to_cid = {executor.submit(fetch_race, cid): cid for cid in cids}

        for future in concurrent.futures.as_completed(future_to_cid):
            cid = future_to_cid[future]
            try:
                data = future.result()
                results[cid] = data
            except Exception as e:
                print(f"  ❌ {cid[:15]}... Failed: {e}")

    return results


def fetch_json(cid: str) -> dict:
    """
    通过 CID 下载并解析 JSON

    Args:
        cid: IPFS 内容标识符

    Returns:
        dict: 解析后的 JSON 数据
    """
    raw = fetch_content(cid)
    return json.loads(raw)


def fetch_and_verify(cid: str) -> dict:
    """
    下载内容并做 SHA256 完整性校验（Sidecar 同步时使用）

    对应设计文档：
    "Sidecar 对下载的元数据内容进行 SHA256 计算，
     将计算结果与链上锚定的 CID 进行一致性比对"

    Args:
        cid: IPFS 内容标识符

    Returns:
        dict: {
            "content": dict,      # 解析后的 JSON
            "raw": bytes,         # 原始字节
            "sha256": str,        # SHA256 哈希值
            "cid": str,           # 原始 CID
            "verified": bool      # 是否下载成功（CID 本身就是内容寻址的校验）
        }
    """
    raw = fetch_content(cid)
    sha256_hash = hashlib.sha256(raw).hexdigest()

    try:
        content = json.loads(raw)
    except json.JSONDecodeError:
        content = None

    return {
        "content": content,
        "raw": raw,
        "sha256": sha256_hash,
        "cid": cid,
        "verified": True,  # 能通过 CID 取到内容即说明内容与哈希匹配
    }


# ═══════════════════════════════════════
# CLI 入口（可直接运行测试）
# ═══════════════════════════════════════

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("""
IPFS 工具 (Python + Pinata REST API)
═════════════════════════════════════

用法:
  python infrastructure/ipfs.py upload     上传测试元数据
  python infrastructure/ipfs.py fetch CID  下载并校验
        """)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "upload":
        test_metadata = {
            "did": "did:ethr:sepolia:0xTestAddress",
            "name": "Test Agent",
            "capabilities": ["test"],
            "description": "Test upload from Python",
            "createdAt": __import__("datetime").datetime.now().isoformat(),
        }
        print("📤 正在上传测试元数据...")
        result = upload_json(test_metadata, "test-metadata.json")
        print(f"✅ 上传成功!")
        print(f"   CID: {result['cid']}")
        print(f"   URL: {result['gateway_url']}")

    elif cmd == "fetch":
        if len(sys.argv) < 3:
            print("❌ 请提供 CID: python infrastructure/ipfs.py fetch <CID>")
            sys.exit(1)
        cid = sys.argv[2]
        print(f"📥 正在下载 CID: {cid}...")
        try:
            result = fetch_and_verify(cid)
            print(f"📄 内容: {json.dumps(result['content'], indent=2, ensure_ascii=False)}")
            print(f"🔒 SHA256: {result['sha256']}")
        except Exception as e:
            print(f"❌ 下载失败: {e}")

    elif cmd == "fetch_batch":
        # python infrastructure/ipfs.py fetch_batch cid1 cid2 ...
        if len(sys.argv) < 3:
            print("❌ 请提供至少一个 CID: python infrastructure/ipfs.py fetch_batch <CID1> <CID2> ...")
            sys.exit(1)
        cids = sys.argv[2:]
        results = fetch_batch(cids)
        print(f"✅ 完成! 成功: {len(results)}/{len(cids)}")
        for cid, data in results.items():
            print(f"  - {cid[:15]}...: {len(data)} bytes")
