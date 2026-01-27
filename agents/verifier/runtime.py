import sys
import os
import json
import time
import requests
import uuid
import re
import datetime
import hashlib
import random

# === 路径适配 ===
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(os.path.dirname(current_dir))
if root_dir not in sys.path:
    sys.path.append(root_dir)

# === 引入项目组件 ===
from infrastructure.wallet import IdentityWallet
from infrastructure.validator import DIDValidator
from agents.verifier.definition import create_verifier_resources

# === 全局配置默认值 ===
#HOLDER_API_URL = "http://localhost:5000"
DEFAULT_ROLE = "agent_b_op"

class VerifierRuntime:
    """
    Verifier 运行时核心逻辑封装。
    既可以单机运行，也可以被并发测试脚本调用。
    """
    def __init__(self, role_name, config=None, instance_name=None, data_dir=None, target_holder_url="http://localhost:5000"):
        self.holder_api_url = target_holder_url # Holder API 地址
        self.role_name = role_name
        self.config = config  # 如果为 None，Wallet 会自动加载默认 key.json
        # 用于日志打印的名字，比如 "Verifier-1"
        self.name = instance_name if instance_name else f"Runtime-{role_name}"
        
        # 数据目录设置
        base_dir = data_dir if data_dir else os.path.join(current_dir, "data")
        self.data_dir = base_dir
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
            
        self.probe_templates_file = os.path.join(self.data_dir, "probe_templates.json")
        self.probe_inputs_file = os.path.join(self.data_dir, "probe_inputs.json")
        
        # 组件占位
        self.wallet = None
        self.validator = None
        self.agent_chain = None
        self.judge_chain = None
        
        # 初始化组件
        self._init_components()

    def _init_components(self):
        """初始化 Wallet, Validator 和 AI Chains"""
        # 设置 API Key (如果 config 中有)
        if self.config and self.config.get("qwq_api_key"):
            os.environ["DASHSCOPE_API_KEY"] = self.config.get("qwq_api_key")

        try:
            self.wallet = IdentityWallet(self.role_name, override_config=self.config)
            self.wallet.load_local_vcs(self.data_dir)
            self.validator = DIDValidator()
            # print(f"[{self.name}] Wallet Ready: {self.wallet.did}")
        except Exception as e:
            print(f"[{self.name}] [Fatal] Infrastructure init failed: {e}")
            sys.exit(1)

        # 初始化 AI 资源
        self.agent_chain, self.judge_chain = create_verifier_resources(self.wallet.did)
        if not self.agent_chain or not self.judge_chain:
            print(f"[{self.name}] [Fatal] Failed to initialize AI Chains.")
            sys.exit(1)

    # === 辅助方法：文件与哈希 ===

    def _get_memory_file(self, target_did):
        """
        获取上下文存储路径。
        文件名格式: memory_{Verifier_DID}_{Holder_DID}.json
        确保多进程/多租户模式下，每个 Verifier-Holder 对都有独立文件。
        """
        # 1. 获取自己的 DID (Verifier) 并处理特殊字符
        my_did = self.wallet.did if self.wallet else "unknown_verifier"
        safe_my_did = my_did.replace(":", "_")
        
        # 2. 获取对方的 DID (Holder) 并处理特殊字符
        target_did_str = target_did or "unknown_holder"
        safe_target_did = target_did_str.replace(":", "_")
        
        # 3. 组合文件名
        filename = f"memory_{safe_my_did}_to_{safe_target_did}.json"
        
        return os.path.join(self.data_dir, filename)

    def _append_interaction(self, target_did, req, res):
        file_path = self._get_memory_file(target_did)
        existing_data = []
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
            except: pass
        
        existing_data.append(req)
        existing_data.append(res)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(existing_data, f, indent=2, ensure_ascii=False)

    def _get_local_snapshot_hash(self, target_did):
        file_path = self._get_memory_file(target_did)
        if not os.path.exists(file_path):
            return hashlib.sha256(json.dumps([]).encode('utf-8')).hexdigest()
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            serialized = json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
            return hashlib.sha256(serialized.encode('utf-8')).hexdigest()
        except:
            return hashlib.sha256(json.dumps([]).encode('utf-8')).hexdigest()

    def _save_vc_to_wallet(self, vc_data_or_list):
        """
        保存 VC 到磁盘 并 同步到内存
        支持单个对象或对象列表
        文件名格式: vc_{DID}_{VC_Type}.json
        """
        # 1. 统一转为列表处理 (因为 Issuer 返回的是 List)
        items = vc_data_or_list if isinstance(vc_data_or_list, list) else [vc_data_or_list]
        
        for vc_data in items:
            safe_did = self.wallet.did.replace(":", "_")
            vc_types = vc_data.get("type", ["UnknownCredential"])
            vc_type_name = vc_types[-1] if isinstance(vc_types, list) else str(vc_types)
            filename = f"vc_{safe_did}_{vc_type_name}.json"
            vc_file = os.path.join(self.data_dir, filename)

            try:
                # A. 存入磁盘
                with open(vc_file, 'w', encoding='utf-8') as f:
                    json.dump(vc_data, f, indent=2, ensure_ascii=False)
                
                # B. 同步到内存 Wallet 对象
                self.wallet.add_vc(vc_data)
                
            except Exception as e:
                print(f"[{self.name}] Failed to save VC: {e}")

    # === 辅助方法：Probe 构造与验证 ===

    def _load_probe_config(self):
        # 创建默认文件以防缺失
        if not os.path.exists(self.probe_templates_file):
            default_tpl = [{"template_id": "tpl_01", "template_str": "Calculate SHA256 of '{{input_text}}'."}]
            with open(self.probe_templates_file, 'w', encoding='utf-8') as f: json.dump(default_tpl, f)
        if not os.path.exists(self.probe_inputs_file):
            default_inp = [{"text": "Hello World", "category": "basic"}]
            with open(self.probe_inputs_file, 'w', encoding='utf-8') as f: json.dump(default_inp, f)

        try:
            with open(self.probe_templates_file, 'r', encoding='utf-8') as f: tpls = json.load(f)
            with open(self.probe_inputs_file, 'r', encoding='utf-8') as f: inps = json.load(f)
            return tpls, inps
        except:
            return [], []

    def _construct_probe_payload(self):
        templates, inputs = self._load_probe_config()
        if not templates or not inputs:
            # Fallback
            templates = [{"template_str": "Echo '{{input_text}}'"}]
            inputs = [{"text": "Test"}]

        template_data = random.choice(templates)
        input_data = random.choice(inputs)
        
        input_text = input_data["text"]
        raw_template = template_data["template_str"]
        final_prompt = raw_template.replace("{{input_text}}", input_text)
        
        # 处理工具占位符
        required_tools = template_data.get("required_tool_names", [])
        for i, tool_name in enumerate(required_tools):
            final_prompt = final_prompt.replace(f"{{{{required_tools[{i}]}}}}", tool_name)
        
        # 动态超时计算
        dynamic_timeout = 2000 + (len(input_text) * 50) + (2000 if required_tools else 0)
        dynamic_timeout = max(3000, min(dynamic_timeout, 100000))

        task_id = f"task-{uuid.uuid4()}"
        payload = {
            "task_id": task_id,
            "prompt": final_prompt,
            "verifier_did": self.wallet.did,
            "timestamp": time.time(),
            "timeout_ms": int(dynamic_timeout)
        }
        
        expected_hash = hashlib.sha256(input_text.encode('utf-8')).hexdigest()
        
        # 返回 raw_input_text 用于 AI 审计
        return payload, expected_hash, final_prompt, input_text, int(dynamic_timeout)

    def _verify_tool_outputs(self, response_text, expected_hash):
        details = []
        passed = True
        
        # Hash Check
        if expected_hash in response_text:
            details.append("Hash Match")
        else:
            passed = False
            details.append(f"Hash Mismatch (Exp: {expected_hash[:6]}...)")
            
        # Time Check (120s tolerance)
        match = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", response_text)
        if match:
            try:
                dt_str = match.group(1)
                dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
                now = datetime.datetime.now(datetime.timezone.utc)
                if abs((now - dt).total_seconds()) <= 120:
                    details.append("Time Fresh")
                else:
                    passed = False
                    details.append("Time Stale")
            except:
                details.append("Time Parse Err")
        else:
            passed = False
            details.append("No Time Found")
            
        return passed, "; ".join(details)

    # === 核心 Action 执行器 ===

    def execute_request_vc(self, issuer_url, credential_type):
        """向 Issuer 申请 VC"""
        print(f"[{self.name}] [Action] Requesting {credential_type} from {issuer_url}...")
        
        payload = {
            "type": "CredentialApplication",
            "credentialType": credential_type,
            "applicant": self.wallet.did,
            "timestamp": time.time(),
            "nonce": str(uuid.uuid4())
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        payload["signature"] = self.wallet.sign_message(serialized)
        
        try:
            resp = requests.post(f"{issuer_url}/issue_vc", json=payload, timeout=30)
            if resp.status_code == 200:
                vc_data = resp.json() # 这是一个 List
                
                self._save_vc_to_wallet(vc_data) 
                
                return True, "VC Received"
            else:
                return False, f"Issuer Error: {resp.status_code}"
        except Exception as e:
            return False, f"Request Failed: {str(e)}"

    def execute_auth(self):
        """执行身份认证"""
        nonce = str(uuid.uuid4())
        req = { 
            "nonce": nonce, "verifier_did": self.wallet.did, 
            "timestamp": time.time(), "type": "AuthRequest" 
        }
        serialized = json.dumps(req, sort_keys=True, separators=(',', ':'))
        req["verifier_signature"] = self.wallet.sign_message(serialized)
        
        t_send = time.time()
        try:
            resp = requests.post(f"{self.holder_api_url}/auth", json=req, timeout=60)
            t_recv = time.time()
            
            if resp.status_code != 200:
                return False, f"HTTP {resp.status_code}", None, (t_send, t_recv, t_recv)
            
            vp = resp.json()
            is_valid, reason = self.validator.verify_vp(vp, nonce)
            holder_did = vp.get("holder", {}).get("id") if isinstance(vp.get("holder"), dict) else vp.get("holder")
            
            self._append_interaction(holder_did, req, vp)
            t_verify = time.time()
            
            if is_valid:
                return True, "Verified", holder_did, (t_send, t_recv, t_verify)
            return False, reason, holder_did, (t_send, t_recv, t_verify)
            
        except Exception as e:
            return False, str(e), None, (t_send, t_send, t_send)

    def execute_probe(self, holder_did):
        """执行探测任务"""
        payload, expected_hash, _, raw_input_text, timeout_ms = self._construct_probe_payload()
        
        serialized = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        payload["verifier_signature"] = self.wallet.sign_message(serialized)
        
        t_send = time.time()
        try:
            # 请求超时设置略大于任务超时
            req_timeout = (timeout_ms / 1000) + 15
            resp = requests.post(f"{self.holder_api_url}/probe", json=payload, timeout=req_timeout)
            t_recv = time.time()
            
            if resp.status_code != 200:
                return False, f"HTTP {resp.status_code}", (t_send, t_recv, t_recv, 0)
            
            data = resp.json()
            result_text = data.get("execution_result", "")
            self._append_interaction(holder_did, payload, data)
            
            # 1. 工具验证
            passed, msg = self._verify_tool_outputs(result_text, expected_hash)
            
            # 2. AI 审计
            if passed:
                try:
                    ai_res = self.judge_chain.invoke({
                        "original_text": raw_input_text,
                        "agent_response": result_text
                    })
                    content = ai_res.content.strip()
                    # 清理 Markdown
                    if content.startswith("```json"): content = content[7:-3]
                    elif content.startswith("```"): content = content[3:-3]
                    
                    audit_res = json.loads(content)
                    if audit_res.get("passed"):
                        msg += f" (Audit: {audit_res.get('reason')})"
                    else:
                        passed = False
                        msg = f"Audit Fail: {audit_res.get('reason')}"
                except Exception as e:
                    msg += f" (Audit Error: {e})"
            
            # 计算 SLA
            duration_ms = (t_recv - t_send) * 1000
            sla_ratio = round(duration_ms / timeout_ms, 4)
            t_verify = time.time()
            
            return passed, msg, (t_send, t_recv, t_verify, sla_ratio)
            
        except Exception as e:
            return False, str(e), (t_send, t_send, t_send, 0)

    def execute_context_check(self, holder_did):
        """执行上下文哈希检查"""
        req = { "nonce": str(uuid.uuid4()), "verifier_did": self.wallet.did, "type": "ContextHashCheck" }
        serialized = json.dumps(req, sort_keys=True, separators=(',', ':'))
        req["verifier_signature"] = self.wallet.sign_message(serialized)
        
        t_send = time.time()
        try:
            resp = requests.post(f"{self.holder_api_url}/context_hash", json=req, timeout=30)
            t_recv = time.time()
            
            if resp.status_code != 200:
                return False, f"HTTP {resp.status_code}", (t_send, t_recv, t_recv)
            
            data = resp.json()
            remote_hash = data.get("context_hash")
            local_hash = self._get_local_snapshot_hash(holder_did)
            self._append_interaction(holder_did, req, data)
            
            match = (remote_hash == local_hash)
            msg = "Match" if match else f"Mismatch (L:{local_hash[:6]} R:{remote_hash[:6]})"
            t_verify = time.time()
            
            return match, msg, (t_send, t_recv, t_verify)
        except Exception as e:
            return False, str(e), (t_send, t_send, t_send)

    # === 主运行循环 ===

    def run(self, max_turns=10, barrier=None, stats_queue=None):
        """
        统一的主循环入口。
        :param max_turns: 最大对话轮数
        :param barrier: 多进程同步栅栏 (用于压测)
        :param stats_queue: 统计数据队列 (用于压测)
        """
        
        # 1. 压测模式下的起跑线同步
        if barrier:
            print(f"[{self.name}] Init done, waiting for others...")
            try:
                worker_id = barrier.wait(timeout=600)
                if worker_id == 0:
                    print("\n" + "="*20 + " ALL READY -> GO " + "="*20 + "\n")
            except Exception as e:
                print(f"[{self.name}] Barrier timeout: {e}")
                return
        
        current_input = "Session Started. Ready."
        chat_history = []
        target_holder_did = None
        turn = 0
        
        # 统计计时器
        t_start_loop = time.time()
        t_auth_done = 0
        t_probe_done = 0
        my_stats = {}

        while turn < max_turns:
            turn += 1
            # 压测时增加随机抖动避免瞬时拥塞
            # if barrier: time.sleep(random.uniform(0.1, 0.5))

            # 1. 思考 (LLM)
            chat_history.append({"role": "user", "content": current_input})
            try:
                response = self.agent_chain.invoke({"messages": chat_history})
                decision_text = response.content if hasattr(response, 'content') else str(response)
                chat_history.append({"role": "assistant", "content": decision_text})
                # 单机模式下打印思考过程，压测模式为了清爽可以注释掉
                if not barrier: print(f"    [Agent] {decision_text}")
            except Exception as e:
                print(f"[{self.name}] Agent Error: {e}")
                break

            # 2. 解析指令
            cmd_line = ""
            for line in decision_text.split('\n'):
                if "COMMAND:" in line: cmd_line = line.strip(); break
            
            if not cmd_line:
                current_input = "Error: Output 'COMMAND:' line."
                continue
            
            if not barrier: print(f"[{self.name}] Turn {turn} | CMD: {cmd_line}")

            # 3. 执行指令
            if "REQUEST_VC" in cmd_line:
                try:
                    parts = cmd_line.split("|")
                    if len(parts) < 3:
                        current_input = "Error: Invalid Format."
                    else:
                        url = parts[1].strip()
                        ctype = parts[2].strip()
                        success, msg = self.execute_request_vc(url, ctype)
                        if success:
                            current_input = f"System: VC '{ctype}' acquired. Proceed."
                            #print(f"[{self.name}] ✅ VC Acquired")
                        else:
                            current_input = f"System: VC Failed. {msg}"
                except Exception as e:
                    current_input = f"Error: {e}"

            elif "INITIATE_AUTH" in cmd_line:
                success, msg, h_did, times = self.execute_auth()
                if success:
                    target_holder_did = h_did
                    current_input = f"Auth SUCCESS. Holder: {h_did}. {msg}"
                    print(f"[{self.name}] ✅ Auth Passed")
                    
                    # 统计
                    t1, t2, t3 = times
                    my_stats["T1"] = t1 - t_start_loop
                    my_stats["T2"] = t2 - t1
                    my_stats["T3"] = t3 - t2
                    my_stats["T4"] = my_stats["T2"] + my_stats["T3"]
                    t_auth_done = t3
                else:
                    current_input = f"Auth FAILED. {msg}"

            elif "INITIATE_PROBE" in cmd_line:
                if not target_holder_did:
                    current_input = "Error: Auth required."
                else:
                    success, msg, times = self.execute_probe(target_holder_did)
                    current_input = f"Probe {'PASS' if success else 'FAIL'}. {msg}"
                    
                    if success:
                        print(f"[{self.name}] ✅ Probe Passed")
                        t1, t2, t3, sla = times
                        if t_auth_done > 0:
                            my_stats["T5"] = t1 - t_auth_done
                            my_stats["T6"] = t2 - t1
                            my_stats["T7"] = t3 - t2
                            my_stats["T8"] = my_stats["T6"] + my_stats["T7"]
                            my_stats["SLA_Load_Ratio"] = sla
                            t_probe_done = t3

            elif "INITIATE_CONTEXT_CHECK" in cmd_line:
                if not target_holder_did:
                    current_input = "Error: Auth required."
                else:
                    success, msg, times = self.execute_context_check(target_holder_did)
                    current_input = f"Context {'PASS' if success else 'FAIL'}. {msg}"
                    
                    if success:
                        print(f"[{self.name}] ✅ Context Passed")
                        t1, t2, t3 = times
                        if t_probe_done > 0:
                            my_stats["T9"] = t1 - t_probe_done
                            my_stats["T10"] = t2 - t1
                            my_stats["T11"] = t3 - t2
                            my_stats["T12"] = my_stats["T10"] + my_stats["T11"]
                            
                            # 压测模式下提交数据
                            if stats_queue:
                                my_stats["Verifier"] = self.name
                                stats_queue.put(my_stats)
                                break # 任务完成退出

            elif "FINISH_AUDIT" in cmd_line:
                print(f"[{self.name}] ✅ Audit Complete.")
                break
            elif "ABORT" in cmd_line:
                print(f"[{self.name}] ❌ Audit Aborted.")
                break
            else:
                current_input = "Unknown Command."

# === 单机运行入口 ===
if __name__ == "__main__":
    try:
        print("="*60)
        print("Starting Standalone Verifier Runtime")
        print("="*60)
        
        # 单机运行时使用默认角色
        runtime = VerifierRuntime(role_name=DEFAULT_ROLE)
        runtime.run()
        
    except KeyboardInterrupt:
        print("\nStopped by user.")
