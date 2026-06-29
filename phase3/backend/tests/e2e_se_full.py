"""SE PoUW 全流程 E2E（真实后端 + 私链 + 真实 LLM 评审，无 mock）。

聚焦 P2(完成铸 NAU) + P3(3 专家 LLM 评审 + 门控):
  发布 SE 任务 -> 指派 -> 提交 -> 完成时触发 3 个对口专长智能体 LLM 评审
  -> 聚合均分达阈值则：华币结算 + 铸 NAU + 标记 COMPLETED；否则判 FAILED 不付款。

断言采用"门控一致性"(status 与评审均分vs阈值一致、付款/铸币与是否通过一致)，
对 LLM 评分波动稳健。P1 自主竞价见 e2e_se_bidding.py。

运行:CWD=phase3/backend -> C:/nautilus-venv/Scripts/python.exe tests/e2e_se_full.py
"""
import os, sys, time
import requests
from jose import jwt
from web3 import Web3
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()
import models.database, models.payment, models.team, models.raid          # noqa: E402,F401
import models.agent_survival, models.partner, models.conversation, models.marketplace_models  # noqa: E402,F401

API = "http://127.0.0.1:8000"
RPC = os.getenv("PRIVATE_RPC", "http://127.0.0.1:8545")
JWT_SECRET = os.getenv("JWT_SECRET"); JWT_ALG = os.getenv("JWT_ALGORITHM", "HS256")
ONE = 10 ** 18
ADMIN = "verify_demo"
REWARD = 5
NAU_EXPECT = 10  # CODE_DEVELOPMENT 的 NAU 奖励(TASK_TYPE_REWARDS)
w3 = Web3(Web3.HTTPProvider(RPC))
BAL_ABI = [{"inputs": [{"name": "a", "type": "address"}], "name": "balanceOf",
            "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"}]
hua = w3.eth.contract(address=Web3.to_checksum_address(os.getenv("HUA_TOKEN_ADDRESS")), abi=BAL_ABI)
nau = w3.eth.contract(address=Web3.to_checksum_address(os.getenv("NAU_TOKEN_ADDRESS")), abi=BAL_ABI)

fail = []
def need(c, m): print(f"  [{'OK' if c else 'FAIL'}] {m}"); (fail.append(m) if not c else None)
def step(m): print(f"\n=== {m} ===")
def tok(u): return jwt.encode({"sub": u, "exp": int(time.time()) + 3600}, JWT_SECRET, algorithm=JWT_ALG)
def auth(u): return {"Authorization": f"Bearer {tok(u)}"}
def reg(u): return requests.post(f"{API}/api/auth/register", json={"username": u, "email": f"{u}@n.ai", "password": "Test@12345"}).status_code
def me(u): return requests.get(f"{API}/api/wallets/me", headers=auth(u)).json()
def hbal(a): return hua.functions.balanceOf(Web3.to_checksum_address(a)).call() / ONE
def nbal(a): return nau.functions.balanceOf(Web3.to_checksum_address(a)).call() / ONE
def mk_agent(u, name, specs):
    r = requests.post(f"{API}/api/agents", headers=auth(u), json={"name": name, "description": "se", "specialties": specs})
    return r.json().get("agent", {}).get("agent_id") if r.status_code == 201 else None

def publish(pub, desc, exp):
    r = requests.post(f"{API}/api/tasks", headers=auth(pub), json={
        "description": desc, "input_data": "REST API 订单服务", "expected_output": exp,
        "reward": REWARD * ONE, "task_type": "CODE_DEVELOPMENT", "timeout": 86400})
    return r.status_code, (r.json().get("id") if r.status_code == 201 else r.text[:120])

def run_case(label, pub, winner, deliverable, db):
    from services.se_pouw_flow import review_result
    from models.database import Task, TaskStatus
    step(f"{label}")
    sc, TID = publish(pub, "实现订单服务下单接口 reverse 校验 + pytest 单测", "可运行且通过单测的代码")
    need(sc == 201, f"发布 SE 任务 HTTP {sc}")
    # 指派(手动 accept，确定执行者)
    need(requests.post(f"{API}/api/tasks/{TID}/accept", headers=auth(winner)).status_code == 200, "中标者接单")
    need(requests.post(f"{API}/api/tasks/{TID}/submit", headers=auth(winner), json={"result": deliverable}).status_code == 200, "提交成果")
    w_addr = me(winner)["address"]; p_addr = me(pub)["address"]
    a0, w0, n0 = hbal(p_addr), hbal(w_addr), nbal(w_addr)
    print(f"  结算前: pub={a0} HUA, winner={w0} HUA, winner NAU={n0}")
    rc = requests.post(f"{API}/api/tasks/{TID}/complete", headers=auth(pub), timeout=180)
    need(rc.status_code == 200, f"完成请求 HTTP {rc.status_code}")
    db.rollback()  # 刷新快照(MySQL 默认 REPEATABLE READ)，读到服务端提交的评审/任务
    res = review_result(db, TID)
    print(f"  评审: n={res['n']} 均分={res['avg']} 通过={res['passed']} 明细={[round(x['score'],1) for x in res['reviews']]}")
    need(res["n"] == 3, "恰好 3 位专家评审打分")
    db.rollback()
    t = db.query(Task).filter(Task.id == TID).first()
    if t is None:
        need(False, "任务可查询"); return res
    a1, w1 = hbal(p_addr), hbal(w_addr)
    n1 = nbal(w_addr)
    if res["passed"]:  # NAU 铸造异步(发交易不等回执)，轮询等其上链
        for _ in range(20):
            if abs((n1 - n0) - NAU_EXPECT) < 1e-9:
                break
            time.sleep(1)
            n1 = nbal(w_addr)
    print(f"  结算后: pub={a1} HUA, winner={w1} HUA, winner NAU={n1}, 任务状态={t.status.value}")
    # 门控一致性：通过 <=> COMPLETED 且付款+铸 NAU；不通过 <=> FAILED 且不付款不铸币
    if res["passed"]:
        need(t.status == TaskStatus.COMPLETED, "通过 -> COMPLETED")
        need(abs((a0 - a1) - REWARD) < 1e-9 and abs((w1 - w0) - REWARD) < 1e-9, f"通过 -> 华币结算 +{REWARD}")
        need(abs((n1 - n0) - NAU_EXPECT) < 1e-9, f"通过 -> 铸 NAU +{NAU_EXPECT}")
    else:
        need(t.status == TaskStatus.FAILED, "不通过 -> FAILED")
        need(abs(a1 - a0) < 1e-9 and abs(w1 - w0) < 1e-9, "不通过 -> 不发生华币结算")
        need(abs(n1 - n0) < 1e-9, "不通过 -> 不铸 NAU")
    return res

def main():
    ts = int(time.time())
    PUB, WIN = f"sf_pub_{ts}", f"sf_win_{ts}"
    step("0. 准备发布者 + 中标智能体 + 充值")
    reg(PUB); reg(WIN)
    from utils.database import SessionLocal
    from models.database import Agent
    db = SessionLocal()
    aid = mk_agent(WIN, f"SEWinner_{ts}", ["python", "代码", "开发", "backend"])
    ag = db.query(Agent).filter(Agent.agent_id == aid).first(); ag.reputation_score = 75.0; db.commit()
    need(aid is not None, f"中标智能体创建 agent_id={aid}")
    if hbal(me(PUB)["address"]) < REWARD * 2:
        rm = requests.post(f"{API}/api/wallets/mint", headers=auth(ADMIN), json={"target": PUB, "amount": REWARD * 4})
        if rm.status_code == 200:
            w3.eth.wait_for_transaction_receipt("0x" + rm.json()["tx_hash"].lstrip("0x"), timeout=30)
    print(f"  发布者 HUA={hbal(me(PUB)['address'])}, 现有智能体数(评审池)>=3")

    # 通过用例：完整、规范、含多用例单测的高质量交付物
    good = '''from dataclasses import dataclass
from typing import List, Dict

@dataclass
class OrderItem:
    sku: str
    qty: int
    price: float

def place_order(items: List[Dict]) -> Dict:
    """创建订单：校验非空、qty>0、price>=0；返回订单详情与总价。

    Raises:
        ValueError: items 为空或存在非法字段时。
    """
    if not items:
        raise ValueError("订单项不能为空")
    parsed, total = [], 0.0
    for it in items:
        sku, qty, price = it.get("sku"), int(it.get("qty", 0)), float(it.get("price", 0))
        if not sku or qty <= 0 or price < 0:
            raise ValueError(f"非法订单项: {it}")
        parsed.append(OrderItem(sku, qty, price))
        total += qty * price
    return {"order_id": 1, "items": [vars(p) for p in parsed], "total": round(total, 2), "status": "created"}

# ---- pytest 单测（成功 + 两个边界）----
import pytest

def test_place_order_success():
    r = place_order([{"sku": "A", "qty": 2, "price": 9.9}])
    assert r["status"] == "created" and r["total"] == 19.8 and r["items"][0]["sku"] == "A"

def test_place_order_empty():
    with pytest.raises(ValueError):
        place_order([])

def test_place_order_invalid_qty():
    with pytest.raises(ValueError):
        place_order([{"sku": "A", "qty": 0, "price": 1.0}])
'''
    run_case("用例 A（高质量交付，期望通过→结算+铸NAU）", PUB, WIN, good, db)

    # 拒绝用例：明显不合格交付物（门控一致性断言对评分波动稳健）
    bad = "（我没有完成这个任务，这里没有任何代码，也没有测试。）"
    run_case("用例 B（不合格交付，验证评分门控）", PUB, WIN, bad, db)

    db.close()
    step("结果汇总")
    if fail:
        print("SE_FULL_FAIL:", fail); sys.exit(1)
    print("SE_FULL_PASS  P2 铸 NAU + P3 三专家评审门控 全流程验证通过")

if __name__ == "__main__":
    main()
