import time
import json
import datetime
import os
import glob
from web3 import Web3
from eth_account.messages import encode_defunct
from infrastructure.load_config import load_key_config


class IdentityWallet:
    """
    数字身份钱包。
    负责管理私钥、消息签名，以及 VC/VP 的本地加载与生成。
    """

    def __init__(self, agent_role_name, w3_provider=None, override_config=None):
        self.w3 = w3_provider if w3_provider else Web3()
        self.config = override_config if override_config else load_key_config()
        self.role_name = agent_role_name

        if agent_role_name not in self.config["accounts"]:
            raise ValueError(f"Role {agent_role_name} not found")
        account_info = self.config["accounts"][agent_role_name]
        self.private_key = account_info["private_key"]

        if agent_role_name.endswith("_op"):
            admin_role = f"{agent_role_name.replace('_op', '')}_admin"
        else:
            admin_role = agent_role_name

        if admin_role in self.config["accounts"]:
            self.did = f"did:ethr:sepolia:{self.config['accounts'][admin_role]['address']}"
        else:
            self.did = f"did:ethr:sepolia:{account_info['address']}"

        self.my_vcs = []

    def _is_minimal_valid_vc(self, vc_data):
        """
        判断 VC 是否满足最小可验证结构，避免加载历史 mock 或无效数据。
        """
        if not isinstance(vc_data, dict):
            return False

        subject = vc_data.get("credentialSubject")
        if not isinstance(subject, dict) or subject.get("id") != self.did:
            return False

        issuer_did = vc_data.get("issuer")
        if not isinstance(issuer_did, str) or not issuer_did.strip():
            return False

        proof = vc_data.get("proof")
        if not isinstance(proof, dict):
            return False

        jws = proof.get("jws")
        if not isinstance(jws, str) or not jws.strip():
            return False

        return True

    def load_local_vcs(self, data_dir):
        """
        从 data 目录加载属于当前 DID 的 VC 文件。
        文件命名格式：vc_{did}_{type}.json
        """
        self.my_vcs = []
        safe_did = self.did.replace(":", "_")
        pattern = os.path.join(data_dir, f"vc_{safe_did}_*.json")
        files = glob.glob(pattern)

        for f_path in files:
            try:
                with open(f_path, 'r', encoding='utf-8') as f:
                    vc_data = json.load(f)
                if self._is_minimal_valid_vc(vc_data):
                    self.my_vcs.append(vc_data)
                else:
                    print(f"[Wallet Warning] Skip unusable VC file: {f_path}")
            except Exception as e:
                print(f"[Wallet Error] Failed to load VC from {f_path}: {e}")

    def add_vc(self, vc_data):
        """动态加入单个 VC。"""
        self.my_vcs.append(vc_data)

    def sign_message(self, text_payload):
        """对文本消息进行以太坊签名。"""
        message = encode_defunct(text=text_payload)
        signed = self.w3.eth.account.sign_message(message, private_key=self.private_key)
        return signed.signature.hex()

    def _filter_vcs_by_type(self, required_vc_types=None):
        """
        按类型筛选需要出示的 VC。
        未指定时默认返回钱包内全部 VC。
        """
        if not required_vc_types:
            return list(self.my_vcs)

        expected = {str(item).strip() for item in required_vc_types if str(item).strip()}
        selected = []
        for vc in self.my_vcs:
            vc_types = vc.get("type", [])
            if isinstance(vc_types, str):
                vc_types = [vc_types]
            normalized = {str(item).strip() for item in vc_types if str(item).strip()}
            if expected.intersection(normalized):
                selected.append(vc)
        return selected

    def create_vp(self, nonce, session=None, holder_binding=None, required_vc_types=None):
        """
        创建 VP。
        支持按需出示 VC，并把 request/session 绑定到 VP 体内，便于后续识别越权请求。
        """
        t_start = time.perf_counter()
        selected_vcs = self._filter_vcs_by_type(required_vc_types)
        if holder_binding is None:
            holder_binding = {"agentDid": self.did}

        vp_payload = {
            "@context": ["https://www.w3.org/2018/credentials/v1"],
            "type": ["VerifiablePresentation"],
            "verifiableCredential": selected_vcs,
            "holder": self.did,
            "holderBinding": holder_binding,
        }
        if session:
            vp_payload["session"] = dict(session)

        serialized_vp = json.dumps(vp_payload, sort_keys=True, separators=(',', ':'))
        signature_hex = self.sign_message(serialized_vp)
        now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        final_vp = vp_payload.copy()
        final_vp["proof"] = {
            "type": "EcdsaSecp256k1RecoverySignature2020",
            "created": now_utc,
            "verificationMethod": f"{self.did}#delegate",
            "proofPurpose": "authentication",
            "challenge": nonce,
            "jws": signature_hex,
        }

        t_end = time.perf_counter()
        return final_vp, (t_end - t_start) * 1000
