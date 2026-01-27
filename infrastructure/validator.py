import json
import subprocess
import datetime
import time
from web3 import Web3
from eth_account.messages import encode_defunct
from infrastructure.load_config import get_resolve_script_path, load_key_config

# 尝试获取全局配置
try:
    from infrastructure.utils import get_w3, get_rpc_url
    global_w3, global_config = get_w3()
    RPC_URL = global_config["api_url"]
except ImportError:
    # 如果找不到 utils，独立加载
    conf = load_key_config()
    RPC_URL = conf["api_url"]
    global_w3 = Web3(Web3.HTTPProvider(RPC_URL))
    get_rpc_url = lambda: (RPC_URL, conf)

class DIDValidator:
    def __init__(self):
        self.w3 = global_w3
        self.resolve_script = get_resolve_script_path()
        self.did_cache = {} # 内存缓存
        
        # 加载受信 Issuer
        key_conf = load_key_config()
        if "issuer" in key_conf["accounts"]:
            self.trusted_issuers = [key_conf["accounts"]["issuer"]["address"].lower()]
        else:
            self.trusted_issuers = []

    def resolve_did(self, did):
        """调用 Node.js 解析 DID (包含重试机制)"""
        # 1. 先查缓存
        if did in self.did_cache:
            #print(f"    [Cache Hit] 直接使用内存缓存: {did}")
            return self.did_cache[did]
        
        # 2. 设置重试参数
        max_retries = 5
        
        # 3. 开始重试循环
        for attempt in range(max_retries):
            # [关键] 每次重试都重新获取一个 RPC 节点 (实现故障转移)
            current_rpc_url, _ = get_rpc_url()
            
            try:
                # 这里的路径已经是绝对路径
                process = subprocess.run(
                    ["node", self.resolve_script, did, current_rpc_url],
                    capture_output=True, text=True, encoding='utf-8'
                )
                
                # 如果成功返回 0，且有输出
                if process.returncode == 0:
                    result = json.loads(process.stdout)
                    if "didDocument" in result:
                        doc = result["didDocument"]
                        self.did_cache[did] = doc # 写入缓存
                        return doc
                
                # 如果失败 (比如 429 Too Many Requests)，打印警告但不退出，继续下一次循环
                print(f"[Validator Warning] 第 {attempt+1} 次解析失败 (节点: {current_rpc_url}): {process.stderr.strip()}")
                
            except Exception as e:
                print(f"[Validator Error] 第 {attempt+1} 次解析异常: {e}")
            
            # 失败后休眠一会，给 RPC 节点或本地 CPU 喘息时间
            if attempt < max_retries - 1:
                time.sleep(1)

        # 4. 如果所有重试都失败了
        print(f"[Validator Fatal] {did} 解析彻底失败，已重试 {max_retries} 次")
        return None

    def check_authorization(self, did_doc, recovered_address):
        """检查 recovered_address 是否是 DID 的 Owner 或 Delegate"""
        if not did_doc or "verificationMethod" not in did_doc:
            return False
        
        target = recovered_address.lower().replace("0x", "")
        authorized = False
        
        for method in did_doc["verificationMethod"]:
            # 1. 检查 Owner (blockchainAccountId)
            if "blockchainAccountId" in method:
                # 格式通常是 did:pkh:eip155:1:0xAddr 或类似于 caip10
                parts = method["blockchainAccountId"].split(":")
                if len(parts) > 0:
                    owner_addr = parts[-1].lower().replace("0x", "")
                    if owner_addr == target:
                        authorized = True
                        break
            
            # 2. 检查 Delegate (publicKeyHex)
            if "publicKeyHex" in method:
                pub_key = method["publicKeyHex"].lower().replace("0x", "")
                if pub_key == target:
                    authorized = True
                    break
        
        return authorized

    def verify_request_signature(self, text_payload, signature, claimed_did):
        """
        通用验签
        """
        if not signature or not claimed_did:
            return False, "缺少签名或DID"

        try:
            msg = encode_defunct(text=text_payload)
            recovered_addr = self.w3.eth.account.recover_message(msg, signature=signature)
            
            doc = self.resolve_did(claimed_did)
            if not doc:
                return False, f"DID文档解析失败: {claimed_did}"
            
            if self.check_authorization(doc, recovered_addr):
                return True, "验证通过"
            
                 # === 缓存失效重试机制 ===
            if claimed_did in self.did_cache:
                print(f"⚠️ [Validator] 缓存中的文档验证失败，清除缓存并重试链上查询: {claimed_did}")
                del self.did_cache[claimed_did]
                
                # 重新解析（强制走网络）
                doc_fresh = self.resolve_did(claimed_did)
                if doc_fresh and self.check_authorization(doc_fresh, recovered_addr):
                    print(f"✅ [Validator] 重试后通过！")
                    return True, "重试通过"
            
            else:
                return False, f"签名者 {recovered_addr} 未被 {claimed_did} 授权"
                
        except Exception as e:
            return False, f"验签过程异常: {str(e)}"

    def verify_vp(self, vp_json, expected_nonce):
        """
        验证 VP (适配 Runtime 接口: 返回 bool, reason)
        """
        # 1. Nonce Check (从 Proof 里取)
        proof = vp_json.get("proof", {})
        if proof.get("challenge") != expected_nonce:
            return False, f"Nonce mismatch: expected {expected_nonce}, got {proof.get('challenge')}"

        # 2. 准备验签数据
        payload = vp_json.copy()
        if "proof" in payload:
            del payload["proof"] # 剔除 proof，剩下的就是 body
        
        # 3. 序列化 (必须与 Wallet.create_vp 一致)
        serialized = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        signature = proof.get("jws")
        
        # 4. 恢复签名者
        holder_did = vp_json.get("holder")
        if isinstance(holder_did, dict): holder_did = holder_did.get("id") # 兼容对象格式

        valid_sig, reason = self.verify_request_signature(serialized, signature, holder_did)
        
        if not valid_sig:
            return False, f"VP Signature Invalid: {reason}"
        
        # 5. VC 验证
        for vc in vp_json.get("verifiableCredential", []):
            vc_res = self._verify_single_vc(vc, holder_did)
            if not vc_res["valid"]:
                 return False, f"VC Invalid: {vc_res['error']}"

        return True, "VP Valid"

    def _verify_single_vc(self, vc, expected_holder):
        res = {"valid": False, "error": ""}
        
        # Subject
        if vc["credentialSubject"]["id"] != expected_holder:
            res["error"] = "Subject ID 不匹配"
            return res
            
        # Time (Fix: use timezone-aware UTC)
        if "validUntil" in vc:
            try:
                # 解析时间字符串 (通常是 2024-12-01T00:00:00Z)
                exp_str = vc["validUntil"].replace("Z", "+00:00")
                exp = datetime.datetime.fromisoformat(exp_str)
                
                # 获取当前带时区的时间
                now = datetime.datetime.now(datetime.timezone.utc)
                
                if now > exp:
                    res["error"] = "VC 已过期"
                    return res
            except Exception as e:
                # 假如解析失败，暂不因为格式问题fail，打印警告
                print(f"[Validator Warning] 日期解析警告: {e}")

        # Issuer Check
        issuer_did = vc["issuer"]
        issuer_addr = issuer_did.split(":")[-1].lower() if ":" in issuer_did else ""
        if issuer_addr not in self.trusted_issuers:
             # 为了严格性，如果不在白名单则报错，或者仅警告
             # res["error"] = f"Issuer {issuer_addr} 不在白名单"
             # return res
             pass

        # Signature
        vc_payload = vc.copy()
        if "proof" in vc_payload:
            del vc_payload["proof"]
        serialized = json.dumps(vc_payload, sort_keys=True, separators=(',', ':'))
        
        valid_sig, reason = self.verify_request_signature(serialized, vc["proof"]["jws"], issuer_did)
        if not valid_sig:
            res["error"] = f"VC签名无效: {reason}"
            return res
            
        res["valid"] = True
        return res
