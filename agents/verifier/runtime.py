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
import shutil


current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(os.path.dirname(current_dir))
if root_dir not in sys.path:
    sys.path.append(root_dir)

from agents.verifier.definition import create_verifier_resources
from infrastructure.runtime_state import (
    IssuerTrustRegistry,
    RuntimeStateManager,
    resolve_runtime_db_path,
)
from infrastructure.validator import DIDValidator
from infrastructure.wallet import IdentityWallet
from interop.request_policy import (
    build_authorization_details,
    build_request_signature_payload,
    compute_authorization_details_hash,
    with_request_envelope,
)


def safe_chain_invoke(chain, payload):
    import time
    import random
    max_retries = 8
    for attempt in range(max_retries):
        try:
            return chain.invoke(payload)
        except Exception as e:
            err_str = str(e).lower()
            if ("429" in err_str or "limit" in err_str or "too many" in err_str) and attempt < max_retries - 1:
                sleep_time = (1.5 ** attempt) + random.uniform(0.5, 2.0)
                print(f"    [Verifier] API Limit hit, waiting {sleep_time:.1f}s (iter {attempt+1})...")
                time.sleep(sleep_time)
                continue
            raise e


DEFAULT_ROLE = "agent_b_op"


class VerifierRuntime:
    """Verifier 运行时核心逻辑封装。"""

    def __init__(
        self,
        role_name,
        config=None,
        instance_name=None,
        data_dir=None,
        target_holder_url="http://localhost:5000",
        state_db_path=None,
    ):
        self.holder_api_url = target_holder_url
        self.role_name = role_name
        self.config = config
        self.name = instance_name if instance_name else f"Runtime-{role_name}"

        base_dir = data_dir if data_dir else os.path.join(current_dir, "data")
        self.data_dir = base_dir
        os.makedirs(self.data_dir, exist_ok=True)
        self.state_db_path = str(state_db_path or resolve_runtime_db_path(self.data_dir))
        self.runtime_state = RuntimeStateManager(self.state_db_path)
        self.trust_registry = IssuerTrustRegistry(self.data_dir)

        self.probe_templates_file = os.path.join(self.data_dir, "probe_templates.json")
        self.probe_inputs_file = os.path.join(self.data_dir, "probe_inputs.json")

        self.wallet = None
        self.validator = None
        self.agent_chain = None
        self.judge_chain = None
        self._init_components()

    def _init_components(self):
        if self.config and self.config.get("qwq_api_key"):
            os.environ["DASHSCOPE_API_KEY"] = self.config.get("qwq_api_key")

        try:
            self.wallet = IdentityWallet(self.role_name, override_config=self.config)
            self.wallet.load_local_vcs(self.data_dir)
            self.validator = DIDValidator(trust_registry=self.trust_registry)
        except Exception as e:
            print(f"[{self.name}] [Fatal] Infrastructure init failed: {e}")
            sys.exit(1)

        self.agent_chain, self.judge_chain = create_verifier_resources(self.wallet.did)
        if not self.agent_chain or not self.judge_chain:
            print(f"[{self.name}] [Fatal] Failed to initialize AI Chains.")
            sys.exit(1)

    def _get_memory_file(self, target_did):
        my_did = self.wallet.did if self.wallet else "unknown_verifier"
        safe_my_did = my_did.replace(":", "_")
        safe_target_did = (target_did or "unknown_holder").replace(":", "_")
        filename = f"memory_{safe_my_did}_to_{safe_target_did}.json"
        return os.path.join(self.data_dir, filename)

    def _append_interaction(self, target_did, req, res, stage="runtime", status="success"):
        try:
            task_id = ""
            if isinstance(req, dict):
                task_id = str(req.get("task_id") or req.get("requestId") or req.get("nonce") or req.get("type") or "")
            self.runtime_state.append_interaction(
                owner_did=self.wallet.did if self.wallet and self.wallet.did else "",
                peer_did=target_did,
                caller_did=self.wallet.did if self.wallet and self.wallet.did else "",
                target_did=target_did,
                request_data=req,
                response_data=res,
                stage=str(stage or "runtime").strip().lower(),
                status=str(status or "success").strip(),
                task_id=task_id,
                source="verifier",
            )
            return
        except Exception:
            pass

        file_path = self._get_memory_file(target_did)
        existing_data = []
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
            except Exception:
                pass

        existing_data.append(req)
        existing_data.append(res)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(existing_data, f, indent=2, ensure_ascii=False)

    def _get_local_snapshot_hash(self, target_did):
        snapshot_hash, item_count = self.runtime_state.get_snapshot_hash(
            owner_did=self.wallet.did if self.wallet and self.wallet.did else "",
            peer_did=target_did,
        )
        if item_count > 0:
            return snapshot_hash

        file_path = self._get_memory_file(target_did)
        if not os.path.exists(file_path):
            return hashlib.sha256(json.dumps([]).encode('utf-8')).hexdigest()
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            serialized = json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
            return hashlib.sha256(serialized.encode('utf-8')).hexdigest()
        except Exception:
            return hashlib.sha256(json.dumps([]).encode('utf-8')).hexdigest()

    def _save_vc_to_wallet(self, vc_data_or_list):
        items = vc_data_or_list if isinstance(vc_data_or_list, list) else [vc_data_or_list]
        for vc_data in items:
            safe_did = self.wallet.did.replace(":", "_")
            vc_types = vc_data.get("type", ["UnknownCredential"])
            vc_type_name = vc_types[-1] if isinstance(vc_types, list) else str(vc_types)
            filename = f"vc_{safe_did}_{vc_type_name}.json"
            vc_file = os.path.join(self.data_dir, filename)
            try:
                with open(vc_file, 'w', encoding='utf-8') as f:
                    json.dump(vc_data, f, indent=2, ensure_ascii=False)
                self.wallet.add_vc(vc_data)
            except Exception as e:
                print(f"[{self.name}] Failed to save VC: {e}")

    def _load_probe_config(self):
        shared_data_dir = os.path.join(current_dir, "data")
        shared_templates = os.path.join(shared_data_dir, "probe_templates.json")
        shared_inputs = os.path.join(shared_data_dir, "probe_inputs.json")

        if not os.path.exists(self.probe_templates_file):
            if os.path.exists(shared_templates):
                shutil.copyfile(shared_templates, self.probe_templates_file)
            else:
                default_tpl = [{"template_id": "tpl_01", "template_str": "Calculate SHA256 of '{{input_text}}'.", "require_time": False}]
                with open(self.probe_templates_file, 'w', encoding='utf-8') as f:
                    json.dump(default_tpl, f)
        if not os.path.exists(self.probe_inputs_file):
            if os.path.exists(shared_inputs):
                shutil.copyfile(shared_inputs, self.probe_inputs_file)
            else:
                default_inp = [{"text": "Hello World", "category": "basic"}]
                with open(self.probe_inputs_file, 'w', encoding='utf-8') as f:
                    json.dump(default_inp, f)

        try:
            with open(self.probe_templates_file, 'r', encoding='utf-8') as f:
                tpls = json.load(f)
            with open(self.probe_inputs_file, 'r', encoding='utf-8') as f:
                inps = json.load(f)
            return tpls, inps
        except Exception:
            return [], []

    def _construct_probe_payload(self):
        templates, inputs = self._load_probe_config()
        if not templates or not inputs:
            templates = [{"template_str": "Echo '{{input_text}}'"}]
            inputs = [{"text": "Test"}]

        template_data = random.choice(templates)
        input_data = random.choice(inputs)
        input_text = input_data["text"]
        raw_template = template_data["template_str"]
        final_prompt = raw_template.replace("{{input_text}}", input_text)

        required_tools = template_data.get("required_tool_names", [])
        for i, tool_name in enumerate(required_tools):
            final_prompt = final_prompt.replace(f"{{{{required_tools[{i}]}}}}", tool_name)

        dynamic_timeout = 2000 + (len(input_text) * 50) + (2000 if required_tools else 0)
        dynamic_timeout = max(3000, min(dynamic_timeout, 100000))

        task_id = f"task-{uuid.uuid4()}"
        payload = {
            "task_id": task_id,
            "prompt": final_prompt,
            "verifier_did": self.wallet.did,
            "timeout_ms": int(dynamic_timeout),
        }
        expected_hash = hashlib.sha256(input_text.encode('utf-8')).hexdigest()

        require_time_check = bool(template_data.get("require_time", False))
        if not require_time_check:
            time_hint_text = f"{raw_template} {' '.join(required_tools)}".lower()
            require_time_check = any(
                hint in time_hint_text
                for hint in ["current utc", "current time", "timestamp", "current_date", "get_current_utc_date"]
            )

        return payload, expected_hash, final_prompt, input_text, int(dynamic_timeout), require_time_check

    def _verify_tool_outputs(self, response_text, expected_hash, require_time_check=False):
        details = []
        passed = True

        if expected_hash in response_text:
            details.append("Hash Match")
        else:
            passed = False
            details.append(f"Hash Mismatch (Exp: {expected_hash[:6]}...)")

        if require_time_check:
            match = re.search(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}Z?)", response_text)
            if match:
                try:
                    dt_str = match.group(1)
                    if "T" in dt_str:
                        dt = datetime.datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                    else:
                        dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
                    now = datetime.datetime.now(datetime.timezone.utc)
                    if abs((now - dt).total_seconds()) <= 120:
                        details.append("Time Fresh")
                    else:
                        passed = False
                        details.append("Time Stale")
                except Exception:
                    passed = False
                    details.append("Time Parse Err")
            else:
                passed = False
                details.append("No Time Found")
        else:
            details.append("Time Check Skipped")

        return passed, "; ".join(details)

    def execute_request_vc(self, issuer_url, credential_type):
        print(f"[{self.name}] [Action] Requesting {credential_type} from {issuer_url}...")
        payload = {
            "type": "CredentialApplication",
            "credentialType": credential_type,
            "applicant": self.wallet.did,
            "timestamp": time.time(),
            "nonce": str(uuid.uuid4()),
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        payload["signature"] = self.wallet.sign_message(serialized)

        try:
            resp = requests.post(f"{issuer_url}/issue_vc", json=payload, timeout=30)
            if resp.status_code == 200:
                self._save_vc_to_wallet(resp.json())
                return True, "VC Received"
            return False, f"Issuer Error: {resp.status_code}"
        except Exception as e:
            return False, f"Request Failed: {str(e)}"

    def _sign_request(self, payload, target_uri):
        serialized = build_request_signature_payload(payload, http_method="POST", target_uri=target_uri)
        request_payload = dict(payload)
        request_payload["verifier_signature"] = self.wallet.sign_message(serialized)
        return request_payload

    def _build_expected_session(self, request_payload):
        return {
            "requestId": str(request_payload.get("requestId") or "").strip(),
            "resource": str(request_payload.get("resource") or "").strip(),
            "action": str(request_payload.get("action") or "").strip(),
            "authorizationDetailsHash": compute_authorization_details_hash(request_payload.get("authorizationDetails")),
            "verifierDid": self.wallet.did,
        }

    def _collect_vp_types(self, vp):
        result = set()
        for vc in vp.get("verifiableCredential", []):
            vc_types = vc.get("type", [])
            if isinstance(vc_types, str):
                vc_types = [vc_types]
            for item in vc_types:
                item = str(item).strip()
                if item:
                    result.add(item)
        return result

    def execute_auth(self):
        nonce = str(uuid.uuid4())
        required_vc_types = ["AgentIdentityCredential", "AgentToolsetCredential"]
        auth_details = build_authorization_details(
            detail_type="vp_presentation",
            actions=["present"],
            locations=[self.holder_api_url],
            datatypes=required_vc_types,
            identifier="holder-auth",
            privileges=["identity", "toolset"],
        )
        req = with_request_envelope(
            {
                "type": "AuthRequest",
                "verifier_did": self.wallet.did,
                "requiredVcTypes": required_vc_types,
            },
            resource="urn:dagg:holder:auth",
            action="authenticate",
            nonce=nonce,
            authorization_details=auth_details,
        )
        req = self._sign_request(req, f"{self.holder_api_url}/auth")

        t_send = time.time()
        try:
            resp = requests.post(f"{self.holder_api_url}/auth", json=req, timeout=60)
            t_recv = time.time()
            if resp.status_code != 200:
                return False, f"HTTP {resp.status_code}", None, (t_send, t_recv, t_recv)

            vp = resp.json()
            expected_session = self._build_expected_session(req)
            is_valid, reason = self.validator.verify_vp(vp, nonce, expected_session=expected_session)
            holder_did = vp.get("holder", {}).get("id") if isinstance(vp.get("holder"), dict) else vp.get("holder")

            if is_valid:
                present_types = self._collect_vp_types(vp)
                missing_types = [item for item in required_vc_types if item not in present_types]
                if missing_types:
                    is_valid = False
                    reason = f"VP 缺少所需 VC: {', '.join(missing_types)}"

            self._append_interaction(holder_did, req, vp, stage="auth", status="success")
            t_verify = time.time()
            if is_valid:
                return True, "Verified", holder_did, (t_send, t_recv, t_verify)
            return False, reason, holder_did, (t_send, t_recv, t_verify)
        except Exception as e:
            return False, str(e), None, (t_send, t_send, t_send)

    def execute_probe(self, holder_did):
        payload, expected_hash, _, raw_input_text, timeout_ms, require_time_check = self._construct_probe_payload()
        probe_details = build_authorization_details(
            detail_type="task-execution",
            actions=["execute"],
            locations=[self.holder_api_url],
            datatypes=["text/plain"],
            identifier=str(payload.get("task_id") or "probe-task"),
            privileges=["probe"],
        )
        payload = with_request_envelope(
            payload,
            resource="urn:dagg:holder:probe",
            action="execute",
            authorization_details=probe_details,
        )
        payload = self._sign_request(payload, f"{self.holder_api_url}/probe")

        t_send = time.time()
        try:
            req_timeout = (timeout_ms / 1000) + 15
            resp = requests.post(f"{self.holder_api_url}/probe", json=payload, timeout=req_timeout)
            t_recv = time.time()
            if resp.status_code != 200:
                return False, f"HTTP {resp.status_code}", (t_send, t_recv, t_recv, 0)

            data = resp.json()
            result_text = data.get("execution_result", "")
            self._append_interaction(holder_did, payload, data, stage="probe", status="success")

            passed, msg = self._verify_tool_outputs(result_text, expected_hash, require_time_check=require_time_check)
            if passed:
                try:
                    ai_res = safe_chain_invoke(
                        self.judge_chain,
                        {
                            "original_text": raw_input_text,
                            "agent_response": result_text,
                        }
                    )
                    content = ai_res.content.strip()
                    if content.startswith("```json"):
                        content = content[7:-3]
                    elif content.startswith("```"):
                        content = content[3:-3]
                    audit_res = json.loads(content)
                    if audit_res.get("passed"):
                        msg += f" (Audit: {audit_res.get('reason')})"
                    else:
                        passed = False
                        msg = f"Audit Fail: {audit_res.get('reason')}"
                except Exception as e:
                    msg += f" (Audit Error: {e})"

            duration_ms = (t_recv - t_send) * 1000
            sla_ratio = round(duration_ms / timeout_ms, 4)
            t_verify = time.time()
            return passed, msg, (t_send, t_recv, t_verify, sla_ratio)
        except Exception as e:
            return False, str(e), (t_send, t_send, t_send, 0)

    def execute_context_check(self, holder_did):
        context_details = build_authorization_details(
            detail_type="context-audit",
            actions=["audit"],
            locations=[self.holder_api_url],
            datatypes=["context-hash"],
            identifier="context-hash-check",
            privileges=["audit"],
        )
        req = with_request_envelope(
            {
                "type": "ContextHashCheck",
                "verifier_did": self.wallet.did,
            },
            resource="urn:dagg:holder:context",
            action="audit",
            authorization_details=context_details,
        )
        req = self._sign_request(req, f"{self.holder_api_url}/context_hash")

        t_send = time.time()
        try:
            resp = requests.post(f"{self.holder_api_url}/context_hash", json=req, timeout=30)
            t_recv = time.time()
            if resp.status_code != 200:
                return False, f"HTTP {resp.status_code}", (t_send, t_recv, t_recv)

            data = resp.json()
            remote_hash = data.get("context_hash")
            local_hash = self._get_local_snapshot_hash(holder_did)
            self._append_interaction(holder_did, req, data, stage="context", status="success")

            match = remote_hash == local_hash
            msg = "Match" if match else f"Mismatch (L:{local_hash[:6]} R:{remote_hash[:6]})"
            t_verify = time.time()
            return match, msg, (t_send, t_recv, t_verify)
        except Exception as e:
            return False, str(e), (t_send, t_send, t_send)

    def run(self, max_turns=10, barrier=None, stats_queue=None):
        if barrier:
            print(f"[{self.name}] Init done, waiting for others...")
            try:
                worker_id = barrier.wait(timeout=600)
                if worker_id == 0:
                    print("\n" + "=" * 20 + " ALL READY -> GO " + "=" * 20 + "\n")
            except Exception as e:
                print(f"[{self.name}] Barrier timeout: {e}")
                return

        current_input = "Session Started. Ready."
        chat_history = []
        target_holder_did = None
        turn = 0

        t_start_loop = time.time()
        t_auth_done = 0
        t_probe_done = 0
        my_stats = {}

        while turn < max_turns:
            turn += 1
            chat_history.append({"role": "user", "content": current_input})
            try:
                response = safe_chain_invoke(self.agent_chain, {"messages": chat_history})
                decision_text = response.content if hasattr(response, 'content') else str(response)
                chat_history.append({"role": "assistant", "content": decision_text})
                if not barrier:
                    print(f"    [Agent] {decision_text}")
            except Exception as e:
                print(f"[{self.name}] Agent Error: {e}")
                break

            cmd_line = ""
            for line in decision_text.split('\n'):
                if "COMMAND:" in line:
                    cmd_line = line.strip()
                    break

            if not cmd_line:
                current_input = "Error: Output 'COMMAND:' line."
                continue

            if not barrier:
                print(f"[{self.name}] Turn {turn} | CMD: {cmd_line}")

            if "REQUEST_VC" in cmd_line:
                try:
                    parts = cmd_line.split("|")
                    if len(parts) < 3:
                        current_input = "Error: Invalid Format."
                    else:
                        url = parts[1].strip()
                        ctype = parts[2].strip()
                        success, msg = self.execute_request_vc(url, ctype)
                        current_input = f"System: VC '{ctype}' acquired. Proceed." if success else f"System: VC Failed. {msg}"
                except Exception as e:
                    current_input = f"Error: {e}"

            elif "INITIATE_AUTH" in cmd_line:
                success, msg, h_did, times = self.execute_auth()
                if success:
                    target_holder_did = h_did
                    current_input = f"Auth SUCCESS. Holder: {h_did}. {msg}"
                    print(f"[{self.name}] Auth Passed")
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
                        print(f"[{self.name}] Probe Passed")
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
                        print(f"[{self.name}] Context Passed")
                        t1, t2, t3 = times
                        if t_probe_done > 0:
                            my_stats["T9"] = t1 - t_probe_done
                            my_stats["T10"] = t2 - t1
                            my_stats["T11"] = t3 - t2
                            my_stats["T12"] = my_stats["T10"] + my_stats["T11"]
                            if stats_queue:
                                my_stats["Verifier"] = self.name
                                stats_queue.put(my_stats)
                                break

            elif "FINISH_AUDIT" in cmd_line:
                print(f"[{self.name}] Audit Complete.")
                break

            elif "ABORT" in cmd_line:
                print(f"[{self.name}] Audit Aborted.")
                break

            else:
                current_input = "Unknown Command."


if __name__ == "__main__":
    try:
        print("=" * 60)
        print("Starting Standalone Verifier Runtime")
        print("=" * 60)
        runtime = VerifierRuntime(role_name=DEFAULT_ROLE)
        runtime.run()
    except KeyboardInterrupt:
        print("\nStopped by user.")
