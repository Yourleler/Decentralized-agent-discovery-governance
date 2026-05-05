import json
import subprocess
import datetime
import time
from web3 import Web3
from eth_account.messages import encode_defunct
from infrastructure.load_config import get_resolve_script_path, load_key_config
from infrastructure.runtime_state import IssuerTrustRegistry


try:
    from infrastructure.utils import get_w3, get_rpc_url
    try:
        global_w3, global_config = get_w3()
        RPC_URL = global_config["api_url"]
    except (SystemExit, Exception):
        conf = load_key_config()
        RPC_URL = conf["api_url"]
        global_w3 = Web3()
        get_rpc_url = lambda: (RPC_URL, conf)
except Exception:
    conf = load_key_config()
    RPC_URL = conf["api_url"]
    global_w3 = Web3()
    get_rpc_url = lambda: (RPC_URL, conf)


class DIDValidator:
    def __init__(self, trust_registry: IssuerTrustRegistry | None = None):
        self.w3 = global_w3
        self.resolve_script = get_resolve_script_path()
        self.did_cache = {}
        self.trust_registry = trust_registry

    def resolve_did(self, did):
        """调用 Node.js 解析 DID，并带重试、文件共享缓存和指数退避机制。"""
        if did in self.did_cache:
            return self.did_cache[did]

        import os, uuid, random
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cache_dir = os.path.join(base_dir, ".global_did_cache")
        os.makedirs(cache_dir, exist_ok=True)
        safe_did = did.replace(':', '_')
        cache_file = os.path.join(cache_dir, f"{safe_did}.json")

        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    doc = json.load(f)
                    self.did_cache[did] = doc
                    return doc
            except Exception:
                pass

        max_retries = 8
        for attempt in range(max_retries):
            current_rpc_url, _ = get_rpc_url()
            try:
                process = subprocess.run(
                    ["node", self.resolve_script, did, current_rpc_url],
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                )
                if process.returncode == 0:
                    result = json.loads(process.stdout)
                    if "didDocument" in result:
                        doc = result["didDocument"]
                        self.did_cache[did] = doc
                        try:
                            tmp_name = f"{cache_file}.{uuid.uuid4().hex}.tmp"
                            with open(tmp_name, "w", encoding="utf-8") as f:
                                json.dump(doc, f, ensure_ascii=False)
                            os.replace(tmp_name, cache_file)
                        except Exception as cache_e:
                            print(f"[Validator Warning] Cache write error: {cache_e}")
                        return doc
                
                err_msg = process.stderr.strip()
                if len(err_msg) > 300:
                    err_msg = err_msg[:300] + "..."
                print(f"[Validator Warning] 第{attempt + 1}次解析失败(节点: {current_rpc_url}): {err_msg}")
            except Exception as e:
                print(f"[Validator Error] 第{attempt + 1}次解析异常: {e}")

            if attempt < max_retries - 1:
                # 指数退避 + 随机抖动避免打堆
                sleep_time = (2 ** attempt) + random.uniform(0.5, 1.5)
                print(f"[Validator] 触发限流防御，休眠 {sleep_time:.2f} 秒后重试...")
                time.sleep(sleep_time)

        print(f"[Validator Fatal] DID 解析彻底失败，已重试 {max_retries} 次: {did}")
        return None

    def check_authorization(self, did_doc, recovered_address):
        """检查恢复出的地址是否是 DID 的 owner 或 delegate。"""
        if not did_doc or "verificationMethod" not in did_doc:
            return False

        target = recovered_address.lower().replace("0x", "")
        for method in did_doc["verificationMethod"]:
            if "blockchainAccountId" in method:
                parts = method["blockchainAccountId"].split(":")
                owner_addr = parts[-1].lower().replace("0x", "") if parts else ""
                if owner_addr == target:
                    return True

            if "publicKeyHex" in method:
                pub_key = str(method["publicKeyHex"]).lower().replace("0x", "")
                if pub_key == target:
                    return True
        return False

    def verify_request_signature(self, text_payload, signature, claimed_did):
        """通用验签。"""
        if not signature or not claimed_did:
            return False, "缺少签名或 DID"

        try:
            msg = encode_defunct(text=text_payload)
            recovered_addr = self.w3.eth.account.recover_message(msg, signature=signature)

            doc = self.resolve_did(claimed_did)
            if not doc:
                return False, f"DID 文档解析失败: {claimed_did}"

            if self.check_authorization(doc, recovered_addr):
                return True, "验证通过"

            if claimed_did in self.did_cache:
                print(f"[Validator] 缓存文档验签失败，清缓存后重试: {claimed_did}")
                del self.did_cache[claimed_did]
                doc_fresh = self.resolve_did(claimed_did)
                if doc_fresh and self.check_authorization(doc_fresh, recovered_addr):
                    return True, "重试通过"

            return False, f"签名地址 {recovered_addr} 未被 {claimed_did} 授权"
        except Exception as e:
            return False, f"验签过程异常: {str(e)}"

    def _match_expected_session(self, actual_session, expected_session):
        """校验 VP session 绑定字段。"""
        if expected_session in (None, "", [], {}):
            return True, "ok"
        if not isinstance(actual_session, dict):
            return False, "VP 缺少 session 绑定信息"

        for field_name in (
            "requestId",
            "resource",
            "action",
            "authorizationDetailsHash",
            "verifierDid",
        ):
            expected_value = expected_session.get(field_name)
            if expected_value in (None, ""):
                continue
            actual_value = actual_session.get(field_name)
            if str(actual_value) != str(expected_value):
                return False, f"session.{field_name} mismatch: expected {expected_value}, got {actual_value}"
        return True, "ok"

    def verify_vp(self, vp_json, expected_nonce, expected_session=None):
        """
        验证 VP。
        在原有 nonce 校验基础上，补充 request/session 绑定校验。
        """
        proof = vp_json.get("proof", {})
        if proof.get("challenge") != expected_nonce:
            return False, f"Nonce mismatch: expected {expected_nonce}, got {proof.get('challenge')}"

        session_ok, session_reason = self._match_expected_session(
            vp_json.get("session"),
            expected_session,
        )
        if not session_ok:
            return False, session_reason

        payload = vp_json.copy()
        payload.pop("proof", None)
        serialized = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        signature = proof.get("jws")

        holder_did = vp_json.get("holder")
        if isinstance(holder_did, dict):
            holder_did = holder_did.get("id")

        valid_sig, reason = self.verify_request_signature(serialized, signature, holder_did)
        if not valid_sig:
            return False, f"VP Signature Invalid: {reason}"

        vc_list = vp_json.get("verifiableCredential", [])
        if not isinstance(vc_list, list) or len(vc_list) == 0:
            return False, "No verifiableCredential in VP"

        for vc in vc_list:
            vc_res = self._verify_single_vc(vc, holder_did)
            if not vc_res["valid"]:
                return False, f"VC Invalid: {vc_res['error']}"

        return True, "VP Valid"

    def _verify_single_vc(self, vc, expected_holder):
        """验证单个 VC。"""
        res = {"valid": False, "error": ""}

        subject = vc.get("credentialSubject")
        if not isinstance(subject, dict):
            res["error"] = "credentialSubject 缺失或格式错误"
            return res

        subject_id = subject.get("id")
        if not isinstance(subject_id, str) or not subject_id:
            res["error"] = "credentialSubject.id 缺失"
            return res

        if subject_id != expected_holder:
            res["error"] = "Subject ID 不匹配"
            return res

        if "validUntil" in vc:
            try:
                exp_str = vc["validUntil"].replace("Z", "+00:00")
                exp = datetime.datetime.fromisoformat(exp_str)
                now = datetime.datetime.now(datetime.timezone.utc)
                if now > exp:
                    res["error"] = "VC 已过期"
                    return res
            except Exception as e:
                print(f"[Validator Warning] 日期解析警告: {e}")

        issuer_did = vc.get("issuer")
        if not isinstance(issuer_did, str) or not issuer_did:
            res["error"] = "issuer 缺失"
            return res
        if not self.is_issuer_trusted(issuer_did):
            res["error"] = f"Issuer 不受信任: {issuer_did}"
            return res

        proof = vc.get("proof")
        if not isinstance(proof, dict):
            res["error"] = "proof 缺失"
            return res

        jws_sig = proof.get("jws")
        if not isinstance(jws_sig, str) or not jws_sig:
            res["error"] = "proof.jws 缺失"
            return res

        vc_payload = vc.copy()
        vc_payload.pop("proof", None)
        serialized = json.dumps(vc_payload, sort_keys=True, separators=(',', ':'))
        valid_sig, reason = self.verify_request_signature(serialized, jws_sig, issuer_did)
        if not valid_sig:
            res["error"] = f"VC 签名无效: {reason}"
            return res

        res["valid"] = True
        return res

    def is_issuer_trusted(self, issuer_did: str) -> bool:
        """
        判断 issuer 是否受信任。
        未配置 trust registry 时默认放行。
        """
        if not issuer_did:
            return False
        if self.trust_registry is None:
            return True
        return bool(self.trust_registry.is_trusted(issuer_did))
