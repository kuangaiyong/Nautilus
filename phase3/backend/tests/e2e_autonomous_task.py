"""
自治任务全链路端到端测试（无 mock）：

    alice 发布 CODE 任务
      -> 平台自动撮合分配给最佳空闲智能体（task_matcher，约 30s 一轮）
      -> 被指派智能体自动执行（agent_engine：LLM 生成代码 + 真实运行）
      -> 任务自动提交（SUBMITTED），带真实交付物（生成的代码 + 运行输出）

全程无人工干预，验证 自动抢单 + 自动执行 两条链路。

运行：  C:/nautilus-venv/Scripts/python.exe tests/e2e_autonomous_task.py
前置：  后端 127.0.0.1:8000 在线；TASK_AUTO_ASSIGN_ENABLED=true（默认）；
        LLM 网关（LLM_BASE_URL）可达。private chain 非必需（到 SUBMITTED 为止）。
"""
import os
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()

API = os.getenv("E2E_API", "http://127.0.0.1:8000")
PUBLISHER = "alice"
PASSWORD = "Test@12345"
DEADLINE_S = 150  # 自动撮合(≤30s) + 执行(LLM 数十秒) 的宽裕上限

fail = []


def step(m): print(f"\n=== {m} ===", flush=True)
def info(m): print(f"  - {m}", flush=True)
def need(cond, m):
    print(f"  [{'OK' if cond else 'FAIL'}] {m}", flush=True)
    if not cond:
        fail.append(m)


def main():
    step("0. alice 发布 CODE 任务")
    tok = requests.post(f"{API}/api/auth/login",
                        json={"username": PUBLISHER, "password": PASSWORD}).json()["access_token"]
    H = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    body = {
        "description": "实现一个函数 fib(n)，返回第 n 个斐波那契数（fib(0)=0, fib(1)=1）",
        "input_data": "def fib(n: int) -> int",
        "expected_output": "fib(10) == 55",
        "reward": 20 * 10**18, "task_type": "CODE", "timeout": 3600,
    }
    r = requests.post(f"{API}/api/tasks", headers=H, json=body)
    need(r.status_code == 201, f"创建任务 HTTP {r.status_code}")
    task = r.json()
    tid = task["id"]
    need(task["status"] == "OPEN", "初始状态 = OPEN")
    info(f"task.id={tid}")

    step("1. 等待自动撮合分配（OPEN -> ACCEPTED，无人工抢单）")
    accepted = None
    t0 = time.time()
    while time.time() - t0 < DEADLINE_S:
        t = requests.get(f"{API}/api/tasks/{tid}").json()
        if t["status"] != "OPEN":
            accepted = t
            break
        time.sleep(5)
    need(accepted is not None and accepted["status"] in ("ACCEPTED", "SUBMITTED"),
         f"任务被自动分配（{int(time.time()-t0)}s 内，状态={accepted['status'] if accepted else 'OPEN'}）")
    if accepted:
        need(bool(accepted.get("agent")), f"已指派智能体: {accepted.get('agent')}")

    step("2. 等待自动执行并提交（-> SUBMITTED，带真实交付物）")
    final = accepted
    while final and final["status"] not in ("SUBMITTED", "COMPLETED", "FAILED") and time.time() - t0 < DEADLINE_S:
        time.sleep(5)
        final = requests.get(f"{API}/api/tasks/{tid}").json()
    need(final is not None and final["status"] in ("SUBMITTED", "COMPLETED"),
         f"任务自动执行并提交（状态={final['status'] if final else '?'}）")

    if final and final.get("result"):
        result = final["result"]
        info(f"交付物长度={len(result)}")
        need("def fib" in result, "交付物包含 fib 函数实现")
        need("fib(10) = 55" in result or "55" in result, "交付物含真实运行输出（fib(10)=55）")
        print("\n--- 自动生成的交付物（前 500 字）---", flush=True)
        print(result[:500], flush=True)

    step("结果汇总")
    if fail:
        print(f"  AUTONOMOUS_E2E_FAIL  失败 {len(fail)} 项：", flush=True)
        for f in fail:
            print(f"    - {f}", flush=True)
        sys.exit(1)
    print("  AUTONOMOUS_E2E_PASS  自治全链路通过（发布→自动抢单→自动执行→提交）", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
