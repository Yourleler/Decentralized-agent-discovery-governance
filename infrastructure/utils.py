import random
import json
import hashlib
import itertools
from web3 import Web3

# === 引用同级模块 ===
from .load_config import load_key_config

_RPC_CYCLE = None

ETH_PRICE_USD = 2930.0  # 2026/1/29 根据实时行情调整，多来源平均 

def get_rpc_url():
    """
    [修改] 从配置池中轮询获取 RPC 节点
    策略：随机起点 + 顺序轮询 (Random-Start Round-Robin)
    优势：既保证了单一进程内的负载均衡，又防止了多进程启动时产生'惊群效应'
    """
    global _RPC_CYCLE
    config = load_key_config() #从key读取的key.json
    
    # 优先检查是否有节点池
    if "api_url_pool" in config and isinstance(config["api_url_pool"], list) and len(config["api_url_pool"]) > 0:
        pool = config["api_url_pool"]
        
        # 如果是第一次调用（或者新进程启动），初始化迭代器
        if _RPC_CYCLE is None:
            # 1. 为了防止多进程同时启动时都打在第1个节点上，
            #    我们在初始化时随机打乱一下顺序，或者随机选一个起点
            start_index = random.randint(0, len(pool) - 1)
            
            # 2. 创建一个无限循环的迭代器
            # 例如 pool=[A, B, C], start_index=1, 顺序就是 B -> C -> A -> B ...
            rotated_pool = pool[start_index:] + pool[:start_index]
            _RPC_CYCLE = itertools.cycle(rotated_pool)
            
        # 3. 获取下一个节点
        selected_url = next(_RPC_CYCLE)
        return selected_url, config
    
    # 回退到单点配置
    return config["api_url"], config
 
def get_w3():
    """
    初始化 Web3 连接
    现在通过统一的 load_key_config 获取配置，更加健壮
    """
    try:
        rpc_url, config = get_rpc_url()
        
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not w3.is_connected():
            print(f"[Network] 连接失败，请检查 API URL: {rpc_url}")
            exit(1)
        return w3, config
    except Exception as e:
        print(f"[Network] 初始化异常: {e}")
        exit(1)

# ethr:did 注册表地址 (Sepolia)
REGISTRY_ADDRESS = "0x03d5003bf0e79C5F5223588F347ebA39AfbC3818"

# ===  ERC-1056 DID Registry 的 ABI ===
REGISTRY_ABI = [
    {
        "constant": False,
        "inputs": [
            {"internalType": "address", "name": "identity", "type": "address"},
            {"internalType": "address", "name": "newOwner", "type": "address"}
        ],
        "name": "changeOwner",
        "outputs": [],
        "payable": False,
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [{"internalType": "address", "name": "identity", "type": "address"}],
        "name": "identityOwner",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [
            {"internalType": "address", "name": "identity", "type": "address"},
            {"internalType": "bytes32", "name": "name", "type": "bytes32"},
            {"internalType": "bytes", "name": "value", "type": "bytes"},
            {"internalType": "uint256", "name": "validity", "type": "uint256"}
        ],
        "name": "setAttribute",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "address", "name": "identity", "type": "address"},
            {"indexed": False, "internalType": "address", "name": "owner", "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "previousChange", "type": "uint256"}
        ],
        "name": "DIDOwnerChanged",
        "type": "event"
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "address", "name": "identity", "type": "address"},
            {"indexed": False, "internalType": "bytes32", "name": "name", "type": "bytes32"},
            {"indexed": False, "internalType": "bytes", "name": "value", "type": "bytes"},
            {"indexed": False, "internalType": "uint256", "name": "validTo", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "previousChange", "type": "uint256"}
        ],
        "name": "DIDAttributeChanged",
        "type": "event"
    },
    {
        "constant": True,
        "inputs": [
            {"internalType": "address", "name": "identity", "type": "address"},
            {"internalType": "bytes32", "name": "delegateType", "type": "bytes32"},
            {"internalType": "address", "name": "delegate", "type": "address"}
        ],
        "name": "validDelegate",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# === 通用内存管理函数 ===

def load_memory(file_path):
    """安全加载 JSON 文件，若不存在返回空列表"""
    import os # 局部引入，保持整洁
    if not os.path.exists(file_path):
        return []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[Warning] Failed to load memory from {file_path}: {e}")
        return []

def save_memory(file_path, memory_data):
    """保存数据到 JSON 文件"""
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(memory_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Error] Failed to save memory to {file_path}: {e}")

def calculate_memory_hash(memory_data):
    """计算哈希，用于签名和校验"""
    serialized = json.dumps(
        memory_data, 
        sort_keys=True, 
        separators=(',', ':'), 
        ensure_ascii=False
    )
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()
