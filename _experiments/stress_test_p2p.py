import sys
import os
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = current_dir
while not os.path.exists(os.path.join(project_root, "infrastructure")):
    parent = os.path.dirname(project_root)
    if parent == project_root: break 
    project_root = parent
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import json
import csv
import multiprocessing
import time
from multiprocessing import Process

# === 引入 Runtime 类 ===
from agents.verifier.runtime import VerifierRuntime

# === 全局配置 ===
VERIFIERS_KEY_PATH = os.path.join(project_root, "data", "verifiers_key.json")
result_dir = os.path.join(current_dir, "result")
if not os.path.exists(result_dir):
    os.makedirs(result_dir)
CSV_REPORT_PATH = os.path.join(result_dir, "p2p_1.csv")

# 压测规模
MAX_PAIRS = 1

def run_p2p_worker(name, config, role_name, stats_queue, barrier, target_url):
    """
    Verifier 工作进程
    Verifier(N) -> Holder(N)
    """
    try:
        # 初始化 Verifier Runtime
        verifier = VerifierRuntime(
            role_name=role_name, 
            config=config, 
            instance_name=name,
            target_holder_url=target_url
        )
        
        # 启动运行
        verifier.run(max_turns=12, barrier=barrier, stats_queue=stats_queue)
        
    except Exception as e:
        print(f"[{name}] Crash: {e}")

def main():
    print("="*60)
    print(f"P2P STRESS TEST | Mode: 1 Verifier vs 1 Holder")
    print("="*60)
    
    # 1. 加载 Verifier 密钥
    if not os.path.exists(VERIFIERS_KEY_PATH):
        print(f"[Error] Key file not found: {VERIFIERS_KEY_PATH}")
        sys.exit(1)
        
    with open(VERIFIERS_KEY_PATH, 'r', encoding='utf-8') as f:
        full_config = json.load(f)
        
    # 2. 筛选并排序角色
    accounts = full_config.get("accounts", {})
    verifier_roles = [k for k in accounts.keys() if "_op" in k and "verifier" in k]
    try:
        verifier_roles.sort(key=lambda x: int(x.split('_')[1]))
    except:
        verifier_roles.sort()
    
    # 截取
    num_pairs = min(MAX_PAIRS, len(verifier_roles))
    active_roles = verifier_roles[:num_pairs]
    
    print(f"Launching {num_pairs} pairs...")
    
    # 3. 准备多进程
    stats_queue = multiprocessing.Queue()
    start_barrier = multiprocessing.Barrier(num_pairs)
    processes = []
    
    # 4. 启动 Verifier 进程，并进行 1对1 端口绑定
    for i, role in enumerate(active_roles):
        # 逻辑映射：Verifier i 攻击 Holder i
        # Holder 端口从 5000 开始：5000, 5001, ..., 5000+n-1
        target_port = 5000 + i
        target_url = f"http://localhost:{target_port}"
        
        p_name = f"Verifier-{i+1}"
        
        p = Process(
            target=run_p2p_worker, 
            args=(p_name, full_config, role, stats_queue, start_barrier, target_url)
        )
        processes.append(p)
        p.start()
        
        if (i + 1) % 20 == 0:
            time.sleep(0.5) # 少量流控，防止启动瞬间卡死

    # print(f"\n[Main] All {num_pairs} Verifiers spawned. Waiting for results...")
    
    # 5. 收集数据
    results = []
    collected_count = 0
    start_wait = time.time()
    
    # 等待直到收集齐数据或超时 (每对预计耗时 10-20秒，总超时设宽松点)
    while collected_count < num_pairs:
        try:
            if time.time() - start_wait > 300: # 5分钟总超时
                print("\n[Main] ⚠️ Global Timeout reached!")
                break
                
            data = stats_queue.get(timeout=10)
            results.append(data)
            collected_count += 1
            sys.stdout.write(f"\r[Main] Completed: {collected_count}/{num_pairs}")
            sys.stdout.flush()
        except:
            pass # 继续等待
            
    print("\n[Main] Collection done. Cleaning up...")

    # 6. 清理进程
    for p in processes:
        if p.is_alive():
            p.terminate()
        p.join()

    # 7. 生成报告
    if results:
        # 1. 预处理数据：计算 Total_Duration
        processed_results = []
        for row in results:
            # 使用 get 获取，防止部分流程失败导致键不存在，默认为 0
            t4 = row.get("T4") or 0
            t8 = row.get("T8") or 0
            t12 = row.get("T12") or 0
            
            # 计算总时长 (三个阶段之和)
            row["Total_Duration"] = t4 + t8 + t12
            processed_results.append(row)

        # 2. 定义 CSV 表头 (新增 Total_Duration)
        fieldnames = [
            "Verifier", 
            "T1", "T2", "T3", "T4",    # Auth Phase
            "T5", "T6", "T7", "T8",    # Probe Phase
            "T9", "T10", "T11", "T12", # Context Phase
            "SLA_Load_Ratio",
            "Total_Duration"           # 总时长
        ]
        
        # 3. 写入 CSV
        try:
            with open(CSV_REPORT_PATH, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for row in processed_results:
                    # 格式化浮点数，保留4位小数
                    formatted = {
                        k: (f"{v:.4f}" if isinstance(v, float) else v) 
                        for k, v in row.items()
                    }
                    writer.writerow(formatted)
            print(f"✅ Report saved to: {CSV_REPORT_PATH}")
            
        except Exception as e:
            print(f"Error saving report: {e}")

        # 4. 终端打印性能指标 (TPS & Max Latency)
        print("\n" + "="*80)
        print(f"{'Phase / Metric':<25} | {'TPS (txn/s)':<15} | {'Avg Latency (s)':<15} | {'Max Latency (s)':<15} | {'Count':<8}")
        print("-" * 80)

        # 定义需要统计的四个维度
        metrics_config = [
            ("Auth Phase (T4)", "T4"),
            ("Probe Phase (T8)", "T8"),
            ("Context Phase (T12)", "T12"),
            ("Full Process (Total)", "Total_Duration")
        ]

        for label, key in metrics_config:
            # 筛选出该阶段成功的数据 (值 > 0)
            valid_values = [r[key] for r in processed_results if r.get(key) is not None and r[key] > 0]
            
            if valid_values:
                count = len(valid_values)
                # 并发场景下，这就取决于最慢的那个请求耗时
                max_val = max(valid_values)
                avg_val = sum(valid_values) / count
                
                # TPS = 完成的总事务数 / 完成这批事务的总时间(即最大延迟)
                tps = count / max_val if max_val > 0 else 0.0
                
                print(f"{label:<25} | {tps:<12.2f} | {avg_val:<15.4f} | {max_val:<15.4f} | {count:<8}")
            else:
                print(f"{label:<25} | {'N/A':<15} | {'N/A':<15} | {'N/A':<15} | {'0':<8}")
        
        print("="*80)

    else:
        print("No results collected.")

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
