"""
任务全生命周期端到端测试（私有链，无 mock）：

    alice 发布开发任务
      -> 多个智能体抢单（先到先得，恰好一个胜出，其余被拒）
      -> 胜出智能体实现并提交结果
      -> 评委（发布者 alice）评审通过
      -> 链上华币奖励结算（publisher 托管钱包 -> 智能体地址）
      -> 生存系统记录收入

动作全部经真实后端 HTTP API；余额/状态由直连私链 + 读 DB 核验。
认证用 JWT_SECRET 本地签发真实 token（与 /api/auth/login 产出一致），
避免登录往返与限流；这是验证手段，非业务 mock。

运行：  C:/nautilus-venv/Scripts/python.exe tests/e2e_task_lifecycle.py
前置：  后端在 127.0.0.1:8000、私链在 PRIVATE_RPC、verify_demo 为管理员
"""
import os
import sys
import time

import requests
from jose import jwt
from web3 import Web3
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 让本地 utils/models 可导入
load_dotenv()

API = os.getenv("E2E_API", "http://127.0.0.1:8000")
RPC = os.getenv("PRIVATE_RPC", "http://127.0.0.1:8545")
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALG = os.getenv("JWT_ALGORITHM", "HS256")
HUA = Web3.to_checksum_address(os.getenv("HUA_TOKEN_ADDRESS"))
ONE = 10 ** 18

w3 = Web3(Web3.HTTPProvider(RPC))
BAL_ABI = [{"inputs": [{"name": "a", "type": "address"}], "name": "balanceOf",
            "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"}]
hua = w3.eth.contract(address=HUA, abi=BAL_ABI)

PUBLISHER = "alice"
OWNERS = ["e2e_dev_a", "e2e_dev_b"]
PASSWORD = "Test@12345"
ADMIN = "verify_demo"
REWARD_HUA = 100  # 100*10^18 = 1e20 wei，远超 int64 上限(~9.22e18)，验证大额奖励不溢出

fail = []


def tok(username):
    return jwt.encode({"sub": username, "exp": int(time.time()) + 3600}, JWT_SECRET, algorithm=JWT_ALG)


def auth(username):
    return {"Authorization": f"Bearer {tok(username)}"}


def bal(addr):
    return hua.functions.balanceOf(Web3.to_checksum_address(addr)).call() / ONE


def db_one(q, args=()):
    # DB 无关：跟随 DATABASE_URL（私有化已切 MySQL），把 ? 占位转命名参数经 app 引擎执行
    import re
    from sqlalchemy import text
    from utils.database import engine
    params = {}

    def _sub(_m):
        i = len(params)
        params[f"p{i}"] = args[i]
        return f":p{i}"

    qn = re.sub(r"\?", _sub, q)
    with engine.connect() as cx:
        return cx.execute(text(qn), params).mappings().first()


def step(m): print(f"\n=== {m} ===")
def info(m): print(f"  - {m}")
def need(cond, m):
    print(f"  [{'OK' if cond else 'FAIL'}] {m}")
    if not cond:
        fail.append(m)


def me(username):
    r = requests.get(f"{API}/api/wallets/me", headers=auth(username))
    r.raise_for_status()
    return r.json()


def ensure_user(username):
    return requests.post(f"{API}/api/auth/register",
                         json={"username": username, "email": f"{username}@nautilus-corp.com",
                               "password": PASSWORD}).status_code


def ensure_agent(username, name, specialties):
    r = requests.post(f"{API}/api/agents", headers=auth(username),
                      json={"name": name, "description": "E2E 开发智能体", "specialties": specialties})
    if r.status_code == 201:
        return r.json()["agent"]["agent_id"], "created"
    addr = me(username)["address"]
    row = db_one("SELECT agent_id FROM agents WHERE lower(owner)=lower(?)", (addr,))
    return (row["agent_id"] if row else None), f"exists({r.status_code})"


def main():
    # ---- 0. 参与者准备 ----
    step("0. 准备参与者（发布者 alice + 两个抢单智能体）")
    for u in OWNERS:
        sc = ensure_user(u)
        info(f"{u}: register={sc} wallet={me(u)['address']}")
    pub = me(PUBLISHER)
    info(f"{PUBLISHER}(发布者): wallet={pub['address']} hua={pub.get('hua')}")

    agents = {}
    for u in OWNERS:
        aid, how = ensure_agent(u, f"DevBot_{u}", ["Python", "FastAPI"])
        agents[u] = {"agent_id": aid, "owner": me(u)["address"]}
        info(f"{u}: agent_id={aid} ({how}) owner={agents[u]['owner']}")

    if bal(pub["address"]) < REWARD_HUA:
        rm = requests.post(f"{API}/api/wallets/mint", headers=auth(ADMIN),
                           json={"target": PUBLISHER, "amount": REWARD_HUA * 5})
        rm.raise_for_status()
        w3.eth.wait_for_transaction_receipt("0x" + rm.json()["tx_hash"].lstrip("0x"), timeout=30)
    need(bal(pub["address"]) >= REWARD_HUA, f"发布者 alice 华币余额 {bal(pub['address'])} ≥ 奖励 {REWARD_HUA}")

    # ---- 1. 发布任务 ----
    step("1. alice 发布开发任务")
    r = requests.post(f"{API}/api/tasks", headers=auth(PUBLISHER), json={
        "description": "实现一个字符串反转函数 reverse(s)，并附带 pytest 单测",
        "input_data": "函数签名 def reverse(s: str) -> str",
        "expected_output": "通过单测的实现代码",
        "reward": REWARD_HUA * ONE, "task_type": "CODE", "timeout": 86400})
    need(r.status_code == 201, f"创建任务 HTTP {r.status_code}")
    task = r.json()
    TID = task["id"]
    info(f"task.id={TID} task_id={task['task_id']} status={task['status']} reward={task['reward']}")
    need(task["status"] == "OPEN", "任务初始状态 = OPEN")

    # ---- 2. 匹配/推荐 ----
    step("2. 智能体匹配/推荐（合适的智能体）")
    rr = requests.get(f"{API}/api/tasks/{TID}/recommendations", headers=auth(PUBLISHER))
    need(rr.status_code == 200, f"获取推荐智能体 HTTP {rr.status_code}")
    if rr.status_code == 200:
        info(f"推荐候选数={len(rr.json())}，示例={[a.get('name') for a in rr.json()[:3]]}")

    # ---- 3. 抢单竞争 ----
    step("3. 多个智能体抢单（先到先得，一个胜出）")
    results = {}
    for u in OWNERS:
        rc = requests.post(f"{API}/api/tasks/{TID}/accept", headers=auth(u))
        results[u] = rc.status_code
        info(f"{u} 抢单 -> HTTP {rc.status_code} "
             f"({rc.json().get('status') if rc.status_code == 200 else rc.text[:80]})")
    winners = [u for u, c in results.items() if c == 200]
    losers = [u for u, c in results.items() if c != 200]
    need(len(winners) == 1, f"恰好一个智能体抢到任务: winners={winners}")
    need(all(results[u] == 400 for u in losers), f"其余智能体被拒(任务已被抢): {[(u, results[u]) for u in losers]}")
    WIN = winners[0]
    info(f"胜出者: {WIN} (agent_id={agents[WIN]['agent_id']}, owner={agents[WIN]['owner']})")

    # ---- 4. 实现并提交 ----
    step("4. 胜出智能体实现并提交结果")
    deliverable = ("def reverse(s: str) -> str:\n    return s[::-1]\n\n"
                   "# tests\nassert reverse('abc') == 'cba'\nassert reverse('') == ''")
    rs = requests.post(f"{API}/api/tasks/{TID}/submit", headers=auth(WIN), json={"result": deliverable})
    need(rs.status_code == 200, f"提交结果 HTTP {rs.status_code}")
    need(db_one("SELECT status FROM tasks WHERE id=?", (TID,))["status"] == "SUBMITTED", "任务状态 = SUBMITTED")

    # ---- 5. 评审 + 奖励 ----
    step("5. 评委(发布者 alice)评审通过 -> 链上华币奖励结算")
    win_addr = agents[WIN]["owner"]
    a0, w0 = bal(pub["address"]), bal(win_addr)
    inc0 = (db_one("SELECT total_income FROM agent_survival WHERE agent_id=?", (agents[WIN]["agent_id"],)) or {"total_income": 0})["total_income"] or 0
    info(f"结算前: alice={a0} HUA, {WIN}={w0} HUA, survival.total_income={inc0}")
    rc = requests.post(f"{API}/api/tasks/{TID}/complete", headers=auth(PUBLISHER))
    need(rc.status_code == 200, f"评审通过/完成 HTTP {rc.status_code} ({'' if rc.status_code == 200 else rc.text[:160]})")
    if rc.status_code == 200:
        cj = rc.json()
        need(cj["status"] == "COMPLETED", "任务状态 = COMPLETED")
        need(bool(cj.get("blockchain_complete_tx")), f"链上奖励交易已记录: {cj.get('blockchain_complete_tx')}")
        info(f"blockchain_status={cj.get('blockchain_status')}")

    # ---- 6. 链上余额核验 ----
    step("6. 链上余额核验（奖励真实转账）")
    a1, w1 = bal(pub["address"]), bal(win_addr)
    info(f"结算后: alice={a1} HUA, {WIN}={w1} HUA")
    need(abs((a0 - a1) - REWARD_HUA) < 1e-9, f"alice 扣减 {REWARD_HUA} 华币 ({a0}->{a1})")
    need(abs((w1 - w0) - REWARD_HUA) < 1e-9, f"{WIN} 收到 {REWARD_HUA} 华币 ({w0}->{w1})")

    # ---- 7. 生存收入核验 ----
    step("7. 生存系统收入核验")
    sv = db_one("SELECT total_income, tasks_completed FROM agent_survival WHERE agent_id=?", (agents[WIN]["agent_id"],))
    info(f"胜出智能体生存记录: total_income={sv['total_income']} tasks_completed={sv['tasks_completed']}")
    need(int(sv["total_income"]) - int(inc0) == REWARD_HUA * ONE, f"生存收入 +{REWARD_HUA} 华币(wei): {inc0} -> {sv['total_income']}")

    # ---- 汇总 ----
    step("结果汇总")
    if fail:
        print(f"  E2E_FAIL  失败 {len(fail)} 项：")
        for f in fail:
            print(f"    - {f}")
        sys.exit(1)
    print("  E2E_PASS  全流程端到端验证通过（发布→抢单→实现→评审→奖励→收入入账）")
    sys.exit(0)


if __name__ == "__main__":
    main()
