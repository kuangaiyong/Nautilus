"""P1 自主竞价 E2E（真实后端 + 真实 DB，无 mock）。

验证:发布 SE 任务 -> 自主智能体加权投标 -> 择优中标(声誉+专长匹配)。
断言"专长匹配的中声誉者"击败"不匹配的高声誉者"，证明专长加权生效。

运行:CWD=phase3/backend -> C:/nautilus-venv/Scripts/python.exe tests/e2e_se_bidding.py
"""
import os, sys, time
import requests
from jose import jwt
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()
# 注册全套模型，避免 Agent->AgentSurvival 等关系在 mapper 配置时找不到类
import models.database, models.payment, models.team, models.raid          # noqa: E402,F401
import models.agent_survival, models.partner, models.conversation, models.marketplace_models  # noqa: E402,F401
API = "http://127.0.0.1:8000"
JWT_SECRET = os.getenv("JWT_SECRET"); JWT_ALG = os.getenv("JWT_ALGORITHM", "HS256")
ONE = 10 ** 18
fail = []
def need(c, m): print(f"  [{'OK' if c else 'FAIL'}] {m}"); (fail.append(m) if not c else None)
def tok(u): return jwt.encode({"sub": u, "exp": int(time.time()) + 3600}, JWT_SECRET, algorithm=JWT_ALG)
def auth(u): return {"Authorization": f"Bearer {tok(u)}"}
def reg(u): return requests.post(f"{API}/api/auth/register", json={"username": u, "email": f"{u}@n.ai", "password": "Test@12345"}).status_code
def me(u): return requests.get(f"{API}/api/wallets/me", headers=auth(u)).json()
def mk_agent(u, name, specs):
    r = requests.post(f"{API}/api/agents", headers=auth(u), json={"name": name, "description": "se", "specialties": specs})
    return r.json().get("agent", {}).get("agent_id") if r.status_code == 201 else None

def main():
    ts = int(time.time())
    PUB = f"se_pub_{ts}"
    # 三个竞标者：A 专长匹配(代码/开发) 声誉70；B 不匹配(测试) 声誉90；C 不匹配(架构) 声誉50
    devs = {
        "A": {"u": f"se_a_{ts}", "specs": ["python", "代码", "开发"], "rep": 70.0},
        "B": {"u": f"se_b_{ts}", "specs": ["测试", "qa"],            "rep": 90.0},
        "C": {"u": f"se_c_{ts}", "specs": ["架构", "design"],        "rep": 50.0},
    }
    print("=== 0. 准备发布者 + 3 个自主智能体 ===")
    reg(PUB)
    from utils.database import SessionLocal
    from models.database import Agent
    db = SessionLocal()
    # 测试隔离：先关闭所有历史 agent 的自主竞价，避免它们也对本任务投标干扰断言
    db.query(Agent).update({Agent.autonomy_enabled: False}); db.commit()
    for k, d in devs.items():
        reg(d["u"]); d["aid"] = mk_agent(d["u"], f"SEBot_{k}_{ts}", d["specs"])
        ag = db.query(Agent).filter(Agent.agent_id == d["aid"]).first()
        ag.autonomy_enabled = True; ag.reputation_score = d["rep"]
        db.commit()
        print(f"  {k}: agent_id={d['aid']} rep={d['rep']} specs={d['specs']} autonomy=on")
    need(all(d.get("aid") for d in devs.values()), "三个智能体创建成功")

    print("=== 1. 发布 CODE_DEVELOPMENT 软件工程任务 ===")
    r = requests.post(f"{API}/api/tasks", headers=auth(PUB), json={
        "description": "实现订单服务的下单接口 + 单测", "input_data": "REST API",
        "expected_output": "可运行代码", "reward": 10 * ONE,
        "task_type": "CODE_DEVELOPMENT", "timeout": 86400})
    need(r.status_code == 201, f"发布 SE 任务 HTTP {r.status_code} {'' if r.status_code==201 else r.text[:120]}")
    TID = r.json()["id"]
    print(f"  task.id={TID} type=CODE_DEVELOPMENT status={r.json()['status']}")

    print("=== 2. 自主竞价(auto_bid)+ 择优中标(award) ===")
    from services.se_pouw_flow import auto_bid_open_se_tasks, award_se_task
    from models.database import DmasTaskBid, Task, TaskStatus
    n = auto_bid_open_se_tasks(db)
    bids = db.query(DmasTaskBid).filter(DmasTaskBid.task_id == TID).all()
    print(f"  新增投标 {n};本任务投标 {len(bids)}:", [(b.agent_id, round(b.weight,1)) for b in bids])
    need(len(bids) == 3, "3 个智能体均投标")
    winner = award_se_task(db, TID)
    print(f"  中标 agent_id = {winner}（期望 A={devs['A']['aid']}，专长匹配 70+30=100 击败 B 90）")
    need(winner == devs["A"]["aid"], "专长匹配的中声誉者(A)中标，击败不匹配的高声誉者(B)")

    db.expire_all()
    t = db.query(Task).filter(Task.id == TID).first()
    need(t.status == TaskStatus.ACCEPTED, f"任务状态 ACCEPTED")
    need(t.agent and t.agent.lower() == me(devs["A"]["u"])["address"].lower(), "任务指派给中标者 A 的 owner")
    won = db.query(DmasTaskBid).filter(DmasTaskBid.task_id == TID, DmasTaskBid.status == "won").all()
    need(len(won) == 1 and won[0].agent_id == devs["A"]["aid"], "中标投标标记 won，其余 lost")
    db.close()

    print("\n=== 结果 ===")
    if fail:
        print("P1_FAIL:", fail); sys.exit(1)
    print("P1_PASS 自主竞价 + 声誉/专长加权中标 验证通过")

if __name__ == "__main__":
    main()
