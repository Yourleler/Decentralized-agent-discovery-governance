import os
import json
import sys

# 1. 计算项目根目录
current_dir = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(current_dir)

# 将根目录加入环境变量，方便其他模块引用
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

def load_key_config():
    """
    加载 config/key.json
    使用 ROOT_DIR 确保无论在哪里运行脚本都能找到文件
    """
    path = os.path.join(ROOT_DIR, "config", "agents_4_key.json") # 按需修改路径和文件名，日常调试使用的config/key.json，2v2演示全流程使用config/agents_4_key.json;并发测压脚本没有使用该函数，而是直接在脚本内加载指定文件
    
    if not os.path.exists(path):
        raise FileNotFoundError(f"[Config] 错误: 找不到密钥文件，请检查路径: {path}")
    
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def get_resolve_script_path():
    """
    获取 real_resolve.js 的绝对路径
    位置: infrastructure/real_resolve.js
    """
    path = os.path.join(current_dir, "real_resolve.js")
    
    if not os.path.exists(path):
        path_root = os.path.join(ROOT_DIR, "real_resolve.js")
        if os.path.exists(path_root):
            return path_root
            
        raise FileNotFoundError(
            f"[Config] 错误: 找不到 real_resolve.js。\n"
            f"请确保文件位于 {path} 或项目根目录。"
        )
    return path