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
    数字身份钱包
    负责管理私钥、签名、创建 VP、管理 VC
    """
    def __init__(self, agent_role_name, w3_provider=None, override_config=None):
        self.w3 = w3_provider if w3_provider else Web3()
        if override_config:
            self.config = override_config
        else:
            self.config = load_key_config()
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
        功能：
        判断 VC 是否满足本项目最小可验证结构，避免加载历史 mock/脏数据。

        参数：
        vc_data (dict): 待校验的 VC 对象。

        返回值：
        bool: 若 VC 具备 `credentialSubject.id`、`issuer`、`proof.jws` 且主体 DID 匹配当前钱包，则返回 True；否则返回 False。
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
        从指定的 data 目录加载所有属于该 DID 的 VC 文件
        文件模式: vc_*.json
        """
        self.my_vcs = [] # 清空旧的
        
        # 仅加载发给当前 DID 的 VC，避免扫描历史全量文件拖慢启动
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
                
        # print(f"[Wallet] Loaded {len(self.my_vcs)} VCs from {data_dir}")

    def add_vc(self, vc_data):
        """动态添加单个 VC (用于刚申请到 VC 时)"""
        self.my_vcs.append(vc_data)

    def sign_message(self, text_payload):
        """
        对某笔交易签名
        """
        message = encode_defunct(text=text_payload)
        signed = self.w3.eth.account.sign_message(message, private_key=self.private_key)
        return signed.signature.hex()

    def create_vp(self, nonce):
        #创建VP
        t_start = time.perf_counter()#高精度计时
        
        vp_payload = {
            "@context": ["https://www.w3.org/2018/credentials/v1"],
            "type": ["VerifiablePresentation"],
            "verifiableCredential": self.my_vcs, # 这里会自动包含刚刚 load_local_vcs 加载的内容
            "holder": self.did,
        }
        
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
            "jws": signature_hex
        }
        
        t_end = time.perf_counter()
        return final_vp, (t_end - t_start) * 1000
