import os
import sys

os.environ["NO_PROXY"] = "aliyuncs.com,dashscope.aliyuncs.com,localhost,127.0.0.1"

# === LangChain & QwQ 引入 ===
from langchain_qwq import ChatQwQ
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import Runnable 

# === 路径适配 ===
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(os.path.dirname(current_dir))
if root_dir not in sys.path:
    sys.path.append(root_dir)

from infrastructure.load_config import load_key_config

#角色定义 (Role)
#Controller (指挥官): 根据 SYSTEM_PROMPT 定义的状态机，决定当前的步骤。它会输出特定的指令字符串（如 COMMAND: INITIATE_AUTH）给 runtime.py 去执行。
#Judge (裁判): 一个专门的 LLM 调用链，用来评估 Holder 的回答内容是否准确（用于 JUDGE_RESPONSE 阶段）。



# === System Prompt (Controller) ===
#PHASE 0: PREPARATION (准备): 发现缺少凭证时，要求 Holder 去申请 VC (REQUEST_VC)。

#PHASE 1: AUTHENTICATION (认证): 建立连接，开始身份核验 (INITIATE_AUTH)。

#PHASE 2: PROBE TASK (探测/挑战): 身份核验通过后，Verifier 会随机生成一个任务（Probe），考验 Holder 是不是真有本事（防止“有证无能”）。
#PHASE 3: JUDGMENT (裁决): 收到 Holder 的任务结果后，LLM 介入评估，给出 Pass/Fail 以及理由
SYSTEM_PROMPT = """You are an Autonomous Verifier Agent (DID: {did}).
Your role is to drive the audit process by issuing COMMANDS to the Runtime.

You must follow this strict execution protocol:

0. [PHASE 0: PREPARATION]
   - Condition: Missing required credentials.
   - Command: "COMMAND: REQUEST_VC | <Issuer_URL> | <Credential_Type>"

1. [PHASE 1: AUTHENTICATION]
   - Condition: Session start.
   - Command: "COMMAND: INITIATE_AUTH"

2. [PHASE 2: PROBE TASK]
   - Condition: Received "Authentication SUCCESS".
   - Decision: Challenge the Holder with a random task (Runtime handles generation).
   - Command: Output exactly "COMMAND: INITIATE_PROBE" 
     (Do NOT invent the task prompt yourself. Runtime will use a secure template.)

3. [PHASE 3: CONTEXT CHECK]
   - Condition: Received "Probe Result Verified: PASS".
   - Command: "COMMAND: INITIATE_CONTEXT_CHECK"

4. [PHASE 4: CONCLUSION]
   - Condition: Received "Context Check: PASS".
   - Command: "COMMAND: FINISH_AUDIT | PASS | Audit Complete"

FAILURE HANDLING:
- If input contains "FAIL" or "Error", output "COMMAND: ABORT | <Reason>".

NEGATIVE CONSTRAINTS:
- DO NOT output explanations.
- ONLY output the COMMAND string.
"""

# === AI Judge Prompt (Auditor) ===
JUDGE_PROMPT_TEMPLATE = """You are an AI Auditor.
Your task is to evaluate if the 'Agent Response' contains a reasonable summary of the 'Original Text'.

[RULES]
1. The Agent Response contains a text summary followed by metadata (Timestamp, Hash).
2. IGNORE the Timestamp and Hash at the end. Focus ONLY on the summary part.
3. Determine if the summary accurately reflects the main points of the Original Text.

[DATA]
Original Text:
{original_text}

Agent Response:
{agent_response}

[OUTPUT FORMAT]
Return ONLY a JSON object (no markdown, no other text):
{{"passed": true, "reason": "Summary is accurate"}}
OR
{{"passed": false, "reason": "Summary is irrelevant"}}
"""

def create_verifier_resources(did_string):
    """
    统一创建 Verifier 的所有资源 (Controller + Judge)
    :return: (agent_runnable, judge_runnable)
    """
    config = load_key_config()
    api_key = config.get("qwq_api_key") or os.environ.get("DASHSCOPE_API_KEY")
    
    if not api_key:
        print("[Agent] Error: 缺少 qwq_api_key")
        return None, None
        
    os.environ["DASHSCOPE_API_KEY"] = api_key

    try:
        # 1. 初始化 LLM - 从配置读取模型参数
        llm_config = config.get("llm_config", {})
        shared_llm = ChatQwQ(
            model=llm_config.get("verifier_model", "qwen-flash"),
            temperature=llm_config.get("verifier_temperature", 0.00),
            base_url=llm_config.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        )

        # 2. 构建 Controller Chain (负责流程)
        controller_prompt = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT.format(did=did_string)),
            MessagesPlaceholder(variable_name="messages"),#留下一个占位符,调用时可以通过设置messages参数传入对话设置
        ])
        agent_runnable = controller_prompt | shared_llm#用管道符把prompt和llm串起来

        # 3. 构建 Judge Chain (负责审计)
        judge_prompt = ChatPromptTemplate.from_template(JUDGE_PROMPT_TEMPLATE)
        judge_runnable = judge_prompt | shared_llm
        
        # 一次性返回两个能力
        return agent_runnable, judge_runnable
        
    except Exception as e:
        print(f"[Error] Verifier 资源初始化失败: {e}")
        return None, None
