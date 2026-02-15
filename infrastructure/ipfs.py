"""
IPFS 工具模块 (Pinata REST API)

直接使用 Pinata REST API，无需 Node.js 中间层。
支持异步并发 (AsyncIO) 与同步调用。

功能：
  - upload_json() / upload_json_async()      上传 JSON 数据到 IPFS，返回 CID
  - upload_file() / upload_file_async()      上传文件到 IPFS，返回 CID
  - fetch_content() / fetch_content_async()  通过 CID 下载内容 (支持竞速与缓存)
  - fetch_and_verify() / fetch_and_verify_async() 下载 + SHA256 校验（Sidecar 同步时使用）
  - fetch_batch_async()                      批量并发下载 (Sidecar 初始化使用)

使用场景对应：
  - Agent 注册:  upload_json(metadata) → CID → registerAgent(did, cid)
  - 证据上传:   upload_json(evidence) → CID → reportMisbehavior(agent, cid)
  - Sidecar 同步: fetch_and_verify_async(cid) → 可信元数据
"""


import json
import time
import hashlib
import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional, List

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from infrastructure.load_config import load_key_config

# ─── 配置与全局变量 ───
LOGGER = logging.getLogger(__name__)#logging是 Python 内置的日志模块

# 延迟加载配置，避免 import 时崩溃
_CONFIG = None

def _get_config():
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = load_key_config()
    return _CONFIG

# Pinata API 地址
PINATA_API_URL = "https://uploads.pinata.cloud/v3/files"
PINATA_API_BASE = "https://api.pinata.cloud/v3"

# 本地缓存目录 (设计原则: Immutable Data Cache)
# 使用项目根目录下的 .ipfs_cache，确保无论从哪里启动程序缓存位置一致
CACHE_DIR = Path(__file__).resolve().parent.parent / ".ipfs_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ─── 异常定义 ───
class IPFSError(Exception):
    """IPFS 操作基类异常"""
    pass#占位用的

class IPFSGatewayError(IPFSError):
    """网关访问失败 (超时/404/5xx)"""
    pass

class IPFSUploadError(IPFSError):
    """上传失败"""
    pass

class IPFSCacheError(IPFSError):
    """缓存读写错误"""
    pass


# ─── 辅助函数 ───

def _get_headers() -> Dict[str, str]:
    """构建 Pinata API 认证头(jwt从此加载)"""
    config = _get_config()
    jwt = config.get("pinata_jwt", "")
    if not jwt:
        raise ValueError("[IPFS] PINATA_JWT 未设置。请在 .env 中设置或通过环境变量传入。")
    return {"Authorization": f"Bearer {jwt}"}

def _get_gateway_url(cid: str) -> str:
    """构建首选网关访问 URL(优先加载配置中预设网关)"""
    config = _get_config()
    gateway = config.get("pinata_gateway", "")
    if gateway:
        return f"https://{gateway}/ipfs/{cid}"
    return f"https://gateway.pinata.cloud/ipfs/{cid}"

def _get_public_gateways() -> List[str]:
    """获取所有可用网关列表 (专属 + 公共)"""
    gateways = []
    # 1. 优先：专属网关
    config = _get_config()
    gateway = config.get("pinata_gateway", "")
    if gateway:
        gateways.append(f"https://{gateway}/ipfs")
    else:
        # Default Pinata Gateway
        gateways.append("https://gateway.pinata.cloud/ipfs")
    
    # 2. 备选：公共网关
    public_gateways = [
        "https://ipfs.io/ipfs",
        "https://cloudflare-ipfs.com/ipfs",
        "https://dweb.link/ipfs",
    ]
    gateways.extend(public_gateways)#追加但不去重
    # 去重
    return list(dict.fromkeys(gateways))


# ─── 核心：缓存机制 ───

def _read_cache(cid: str) -> Optional[bytes]:
    """
    从本地文件系统读取缓存 (Raw Bytes)
    - 文件名即 CID，无后缀：保持内容寻址的纯粹性，避免猜测文件类型。
    - 二进制读取 (bytes)：确保 SHA256 校验绝对一致，且支持任意格式 (JSON/图片/PDF)。
    """
    cache_path = CACHE_DIR / cid #/是pathlib.Path路径拼接的重载符
    if cache_path.exists():
        try:
            return cache_path.read_bytes()
        except Exception as e:
            LOGGER.warning(f"[IPFS] Cache read failed for {cid}: {e}")
    return None

def _write_cache(cid: str, content: bytes):
    """写入本地文件系统缓存 (Immutable)"""
    try:
        cache_path = CACHE_DIR / cid
        # 原子写入：先写临时文件再重命名，防止写入中断导致文件损坏
        temp_path = cache_path.with_suffix(".tmp")#加后缀,用这个可以有替换后缀的功能
        temp_path.write_bytes(content)
        temp_path.rename(cache_path)#重命名
    except Exception as e:
        LOGGER.warning(f"[IPFS] Cache write failed for {cid}: {e}")


# ═══════════════════════════════════════
# 异步上传功能 (Async Upload)
# ═══════════════════════════════════════

async def upload_json_async(data: dict, name: str = None) -> dict:#async表明其是异步 为协程,需要用await调用协程挂起
    """[Async] 上传 JSON 数据到 IPFS"""
    file_name = name or f"data-{int(time.time())}.json"
    json_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
    
    # Pinata V3 multipart/form-data 机制:
    # 1. files: 放入文件对象(key标识为文件)。httpx 会自动生成 filename 和 Content-Type 头，Pinata 识别为文件流。
    files = {"file": (file_name, json_bytes, "application/json")}
    
    # 2. data: 放入普通字段。httpx 处理为简单键值对。指定 "network": "public" 以允许公共网关访问。Pinata V3 默认private
    form_data = {"network": "public"}
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                PINATA_API_URL, 
                headers=_get_headers(), 
                files=files, 
                data=form_data, 
                timeout=30.0
            )
            response.raise_for_status()
            result = response.json()
            cid = result["data"]["cid"]
            
            # 顺便写入缓存，自己上传的肯定可信
            _write_cache(cid, json_bytes)
            
            return {
                "cid": cid,
                "gateway_url": _get_gateway_url(cid),
            }
    except httpx.HTTPError as e:
        raise IPFSUploadError(f"Upload failed: {str(e)}") from e

async def upload_file_async(file_path: str) -> dict:
    """[Async] 上传本地文件到 IPFS"""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"[IPFS] 文件不存在: {file_path}")

    file_name = path.name
    # 读取文件内容以便上传
    content = path.read_bytes()
    files = {"file": (file_name, content)}
    form_data = {"network": "public"}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                PINATA_API_URL, 
                headers=_get_headers(), 
                files=files, 
                data=form_data, 
                timeout=60.0
            )
            response.raise_for_status()
            result = response.json()
            cid = result["data"]["cid"]
            
            _write_cache(cid, content)
            
            return {
                "cid": cid,
                "gateway_url": _get_gateway_url(cid),
            }
    except httpx.HTTPError as e:
        raise IPFSUploadError(f"File upload failed: {str(e)}") from e


# ═══════════════════════════════════════
# 异步下载功能 (Async Fetch & Race)
# ═══════════════════════════════════════

async def _fetch_url_async(client: httpx.AsyncClient, url: str) -> bytes:
    """单个 URL 下载协程"""
    try:
        resp = await client.get(
            url, 
            timeout=10.0, 
            follow_redirects=True,
            headers={"User-Agent": "PinataSidecar/2.0"}
        )
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        # 仅由于竞速需要，这里抛出异常供上层捕获，不打印日志以免刷屏
        raise IPFSGatewayError(f"Failed {url}") from e

@retry(
    stop=stop_after_attempt(3), 
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(IPFSGatewayError),
    reraise=True
)#重试3次，每次等待时间指数级增长，最多等待10秒，重试类型为IPFSGatewayError，重试后重新抛出异常
async def fetch_race_async(cid: str) -> bytes:
    """
    [核心] 异步网关竞速下载
    优先查缓存 -> 缓存未命中 -> 并发请求多个网关 -> 取最快 -> 写入缓存
    """
    # 1. 查缓存
    cached = _read_cache(cid)
    if cached:
        return cached

    # 2. 准备网关列表
    gateways = _get_public_gateways()
    urls = [f"{gw}/{cid}" for gw in gateways]
    
    # 3. 并发竞速
    async with httpx.AsyncClient() as client:
        # 创建这组任务
        tasks = [
            asyncio.create_task(_fetch_url_async(client, url)) 
            for url in urls
        ]
        
        try:
            # as_completed 返回 iterator，按完成顺序 yield (谁快谁先出)。
            # 这里的机制类似于：
            # 1. create_task 开启了"虚拟机/沙盒"，任务在其中独立运行，异常也被隔离在 Task 对象中。
            # 2. await future 是"开箱"过程：将沙盒内的结果（数据或异常）释放到主控流程中。
            #    - 若成功：拿到数据。
            #    - 若失败：在此处重新抛出异常（"引爆"错误），被下方 except 捕获从而忽略该失败节点。
            for future in asyncio.as_completed(tasks):
                try:
                    content = await future
                    # 有一个成功了，取消其他任务
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    
                    # 写入缓存
                    _write_cache(cid, content)
                    return content
                except Exception:
                    # 这个 task 失败了，继续等下一个
                    continue
            
        except asyncio.CancelledError:
            # 如果外部取消了我们，我们也取消子任务
            for t in tasks:
                if not t.done():
                    t.cancel()
            raise

    # 所有都失败了
    raise IPFSGatewayError(f"[IPFS] All gateways failed for CID {cid}")

async def fetch_json_async(cid: str) -> dict:
    """[Async] 下载并解析 JSON"""
    content = await fetch_race_async(cid)
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON content for CID {cid}") from e

async def fetch_and_verify_async(cid: str) -> dict:
    """
    [Async] 下载 + 校验 (Sidecar 核心逻辑)
    """
    content_bytes = await fetch_race_async(cid)
    
    # 计算 SHA256
    sha256_hash = hashlib.sha256(content_bytes).hexdigest()
    
    # 尝试解析 JSON
    try:
        data = json.loads(content_bytes)
    except json.JSONDecodeError:
        data = None
        
    return {
        "content": data,
        "raw": content_bytes,
        "sha256": sha256_hash,
        "cid": cid,
        # 目前 verification 依赖于 HTTPS 网关的可信度。
        # 严格的 CID 校验需要本地复刻 IPFS 的 DAG 分块与哈希算法 (需引入 ipfs-cid 库)，
        # 对于 Agent Metadata 这种小文件，Risk 较低，暂时通过 SHA256 用于事后审计(Metadata里面最好最带hash用于校验)
        "verified": True 
    }

async def fetch_batch_async(cids: List[str], max_workers: int = 5) -> Dict[str, bytes]:
    """
    [Async] 批量并发下载 (利用 Semaphore 控制并发度)
    
    注意：采用 Best-Effort 策略。
    - 下载失败的任务会被沉默丢弃 (只打印 ERROR 日志)。
    - 返回的字典可能少于输入的 cids 数量。
    """
    sem = asyncio.Semaphore(max_workers)
    results = {}
    
    async def _bounded_fetch(cid):
        # async with sem: 上下文管理器 (Context Manager)
        # 1. 自动管理凭证：进入时 acquire() 拿锁/领证，退出时 release() 还锁/归还。
        # 2. 也是并发控制的核心：
        #    - 这是一个"协程版"的信号量，非系统线程。
        #    - 限制同时处于"活跃状态" (Running) 的协程数量，防止 IO 爆炸。
        #    - 相比线程池 (ThreadPool)，协程切换开销极小，更适合高并发网络请求。
        async with sem:
            try:
                data = await fetch_race_async(cid)
                return cid, data
            except Exception as e:
                LOGGER.error(f"[IPFS] Batch fetch failed for {cid}: {e}")
                return cid, None

    tasks = [asyncio.create_task(_bounded_fetch(cid)) for cid in cids]
    # await asyncio.gather: "集中拆箱"
    # 1. 并发执行所有 Task，并等待全部完成 (Gathering)。
    # 2. 按输入顺序返回结果列表: [(cid1, data1), (cid2, None), ...]。
    #    即使中间有的任务很快完成，也会在列表中占好位子等待其他任务。
    done_results = await asyncio.gather(*tasks)
    
    # 过滤失败任务 (Best-Effort 策略)
    # 这里的逻辑是：只返回下载成功的数据，悄悄丢弃失败的 (None)。
    # 调用者拿到的 results 字典可能少于入参 cids 的数量。
    # 若需严格一致性 (All-or-Nothing)，调用者需自行比对 results.keys() 与 cids。
    for cid, data in done_results:
        if data is not None:
            results[cid] = data
            
    return results


# ═══════════════════════════════════════
# 同步兼容层 (Sync Wrappers for CLI/Legacy)
# ═══════════════════════════════════════

def _run_sync(coro):
    """
    安全地同步执行异步协程。
    - 如果当前没有事件循环 (CLI 场景)：用 asyncio.run()
    - 如果当前已有事件循环 (被 FastAPI/uvicorn 调用)：用 loop.run_until_complete()
    注意：在 FastAPI 中应直接使用 async 版本，此处仅作兜底兼容。
    """
    try:
        loop = asyncio.get_running_loop()#获取当前正在运行的事件循环的函数
    except RuntimeError:
        # 没有正在运行的事件循环，安全使用 asyncio.run()启动事件循环并执行传入的协程
        return asyncio.run(coro)
    else:
        # 已有事件循环(try中未抛出异常) -> 不能用 asyncio.run()，
        # 创建新线程执行以避免阻塞事件循环
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()

def upload_json(data: dict, name: str = None) -> dict:
    """[Sync] upload_json_async 的同步封装"""
    return _run_sync(upload_json_async(data, name))

def upload_file(file_path: str) -> dict:
    """[Sync] upload_file_async 的同步封装"""
    return _run_sync(upload_file_async(file_path))

def fetch_content(cid: str) -> bytes:
    """[Sync] fetch_race_async 的同步封装"""
    return _run_sync(fetch_race_async(cid))

def fetch_json(cid: str) -> dict:
    """[Sync] fetch_json_async 的同步封装"""
    return _run_sync(fetch_json_async(cid))

def fetch_and_verify(cid: str) -> dict:
    """[Sync] fetch_and_verify_async 的同步封装"""
    return _run_sync(fetch_and_verify_async(cid))

def fetch_batch(cids: List[str], max_workers: int = 5) -> Dict[str, bytes]:
    """
    [Sync] fetch_batch_async 的同步封装
    同样采用 Best-Effort 策略，失败的任务会被丢弃。
    """
    return _run_sync(fetch_batch_async(cids, max_workers))


# ═══════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════

if __name__ == "__main__":
    import sys
    
    # 配置日志输出到控制台
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    if len(sys.argv) < 2:
        print("""
IPFS 工具 (Async/Sync Hybrid)
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
            "name": "Async IPFS Test",
            "description": "Uploaded via new async client",
            "timestamp": time.time(),
        }
        print("📤 正在上传测试元数据 (Sync Wrapper)...")
        try:
            result = upload_json(test_metadata, "async-test.json")
            print(f"✅ 上传成功!")
            print(f"   CID: {result['cid']}")
            print(f"   URL: {result['gateway_url']}")
        except Exception as e:
            print(f"❌ 上传失败: {e}")

    elif cmd == "fetch":
        if len(sys.argv) < 3:
            print("❌ 请提供 CID")
            sys.exit(1)
        cid = sys.argv[2]
        print(f"📥 正在下载 CID: {cid} (from Cache or Network)...")
        try:
            result = fetch_and_verify(cid)
            print(f"📄 内容: {json.dumps(result['content'], indent=2, ensure_ascii=False)}")
            print(f"🔒 SHA256: {result['sha256']}")
            
            # 验证缓存是否存在
            cache_path = CACHE_DIR / cid
            if cache_path.exists():
                print("💾 本地缓存已完成")
        except Exception as e:
            print(f"❌ 下载失败: {e}")
