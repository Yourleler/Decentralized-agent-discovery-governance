from rich import print
from _operator import abs
from _operator import add
import os
import sys
import hashlib   
import datetime  

os.environ["NO_PROXY"] = "aliyuncs.com,dashscope.aliyuncs.com,localhost,127.0.0.1"

# === LangChain & QwQ 引入 ===
from langchain_qwq import ChatQwQ
from langchain.agents import create_agent 
from langgraph.checkpoint.memory import InMemorySaver
from langchain.tools import tool

# === 路径适配 ===
# 假设运行目录为项目根目录，确保能导入 infrastructure 和同级模块
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(os.path.dirname(current_dir)) # 定位到项目根目录
if root_dir not in sys.path:
    sys.path.append(root_dir)

from infrastructure.load_config import load_key_config

# === Tools 定义 ===

@tool
def get_hash(text: str) -> str:
    """
    Useful for calculating the SHA-256 hash of a given string.
    Input: The text string to hash.
    Output: The hexadecimal representation of the hash.
    """
    # 模拟工具调用的日志，方便在控制台看到 Agent 的思考过程
    #print(f"\n[Tool] 正在计算哈希: '{text[:15]}...'")
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

@tool
def get_current_utc_date() -> str:
    """
    Useful for getting the current UTC date and time.
    No input required.
    Output: Current UTC timestamp string (e.g., 2024-01-01 12:00:00 UTC).
    """
    #print(f"\n[Tool] 正在获取当前 UTC 时间...")
    # 获取标准 UTC 时间
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def get_holder_tools():
    """
    返回 Holder Agent 可用的工具列表。
    仅包含计算类工具。
    """
    return [get_hash, get_current_utc_date]

# === System Prompt ===
# 核心指令：明确区分三种模式，只负责决策和计算，不负责签名
SYSTEM_PROMPT = """You are an autonomous AI Agent (Holder) with DID: {did}.
Your role is to act as the "Brain" - making decisions and processing information.
The system Runtime (your "Body") handles all cryptographic signing, private keys, and VC presentations.

CRITICAL INSTRUCTIONS - DISTINGUISH THREE MODES:

[MODE 0: PREPARATION]
- Trigger: Input warns "No VC found".
- Goal: Request a Verifiable Credential from the Issuer.
- Action: Output exactly: "COMMAND: REQUEST_VC | <Issuer_URL> | <Credential_Type>"

[MODE 1: AUTHENTICATION]
- Trigger: Input mentions "Authentication Request" or asks for identity verification.
- Goal: Decide whether to present your identity (and VCs) to the Verifier.
- Action:
  1. Analyze the request details (Verifier DID, Nonce).
  2. If you trust the verifier and agree to authenticate, output exactly: "APPROVE".
  3. If you decline, output exactly: "REJECT".

[MODE 2: PROBE TASK]
- Trigger: Input mentions "New Task" or "Task ID".
- Goal: Execute the requested task accurately using available tools.
- Action:
  1. ANALYZE the prompt.
  2. USE calculation tools (e.g., 'get_hash', 'get_current_utc_date') to obtain facts.
  3. WAIT for tool observations.
  4. GENERATE a final plain text summary as the result.
  DO NOT attempt to sign the result. Just output the final answer text.

[MODE 3: CONTEXT CHECK]
- Trigger: Input mentions "Context Hash Request".
- Goal: Decide whether to provide your memory state hash for auditing.
- Action:
  1. If you agree to provide the context hash, output exactly: "APPROVE".
  2. If you decline, output exactly: "REJECT".

Your goal is ACCURACY, CONSISTENCY, and SECURITY. 
"""

def create_holder_agent(did_string):
    """
    创建 Holder Agent (Runtime 托管模式)
    :param did_string: Agent 的 DID，用于填充 System Prompt
    """
    # 1. 加载配置获取 API Key
    config = load_key_config()
    api_key = config.get("qwq_api_key") or os.environ.get("DASHSCOPE_API_KEY")
    
    if not api_key:
        print("[Agent] Error: 缺少 qwq_api_key，请在 key.json 中配置。")
        return None
    
    # 设置环境变量供 LangChain 调用
    os.environ["DASHSCOPE_API_KEY"] = api_key

    try:
        # 2. 初始化 LLM
        # 使用较低的 temperature (0.01) 保证决策的一致性和严谨性
        llm = ChatQwQ(
            model="qwen-plus",
            temperature=0.01,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
        )

        # 3. 设置短时记忆 (Conversation Buffer)
        # 注意：这里是 Agent 思考过程的暂存区，真正的持久化历史由 Runtime 写入磁盘
        checkpointer = InMemorySaver()

        # 4. 获取工具
        # 直接调用本文件内定义的函数
        tools = get_holder_tools()

        # 5. 格式化 Prompt
        formatted_system_prompt = SYSTEM_PROMPT.format(did=did_string)

        # 6. 创建 Agent
        # 使用 LangChain 的 create_agent 封装 ReAct 或 Tool Calling 逻辑
        agent = create_agent(
            model=llm,
            tools=tools,
            system_prompt=formatted_system_prompt,
            checkpointer=checkpointer
        )
        
        return agent
        
    except Exception as e:
        print(f"[Error] Agent 初始化失败: {e}")
        return None
