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

import time
import json
import hashlib
import secrets
import csv

def generate_turn_data(round_idx):
    """
    模拟一轮对话的数据。
    为了保证线性增长测试的准确性，每轮增加的数据量尽量保持稳定。
    假设每轮对话（Prompt + Response）大约 500 字符 (约 120-150 Tokens)。
    """
    return {
        "round_index": round_idx,
        "role": "user",
        "content": f"Please calculate the hash for task {round_idx}..." + secrets.token_hex(64), # 模拟用户输入
        "agent_response": {
            "result": f"The result is {secrets.token_hex(16)}", # 模拟 Agent 回复
            "thought": secrets.token_hex(500), # 模拟中间思考过程
            "timestamp": time.time()
        }
    }

def main():
    print("="*60)
    print("=== 上下文一致性哈希计算：极限压力测试 ===")
    print("="*60)

    # === 配置参数 ===
    MAX_ROUNDS = 35000  
    # 每隔 1000 轮记录一次数据，防止 CSV 文件过大
    SAMPLE_INTERVAL = 1000 
    
    # 模拟内存
    memory_storage = []
    
    # CSV 文件路径 (保存在当前脚本同目录下)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    csv_file_path = os.path.join(current_dir, "benchmark_hash_results.csv")

    print(f"正在进行测试 (总计 {MAX_ROUNDS} 轮)...")
    print(f"{'Round':<8} | {'Tokens(Est)':<12} | {'Size(KB)':<10} | {'Time(ms)':<10}")
    print("-" * 50)

    with open(csv_file_path, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        # 写入表头
        writer.writerow(["Round_Index", "Estimated_Tokens", "JSON_Size_KB", "Calc_Time_ms"])

        for i in range(1, MAX_ROUNDS + 1):
            new_data = generate_turn_data(i)
            memory_storage.append(new_data)

            if i % SAMPLE_INTERVAL == 0:
                
                # --- [核心测量] ---
                t_start = time.perf_counter()
                json_str = json.dumps(memory_storage, sort_keys=True)
                _ = hashlib.sha256(json_str.encode('utf-8')).hexdigest()
                t_end = time.perf_counter()
                duration_ms = (t_end - t_start) * 1000
                
                # --- [辅助指标] ---
                size_bytes = len(json_str)
                size_kb = size_bytes / 1024
                tokens_est = int(size_bytes / 4)

                # --- [写入CSV] ---
                writer.writerow([i, tokens_est, f"{size_kb:.2f}", f"{duration_ms:.4f}"])
                
                # --- [单行刷新] ---
                progress = (i / MAX_ROUNDS) * 100
                sys.stdout.write(f"\r进度: {progress:.1f}% | 当前轮数: {i} | Token估算: {tokens_est/1000000:.2f}M | 耗时: {duration_ms:.2f}ms")
                sys.stdout.flush()

    print("="*60)
    print(f"测试完成！")
    print(f"数据已保存至: {csv_file_path}")
    print("="*60)

if __name__ == "__main__":
    main()