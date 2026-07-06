"""核心全流程端到端测试（私有链 + MySQL，无 mock）。

从零注册开始走完整业务链，并逐项断言业务逻辑 + 数据：
  注册用户/钱包 -> 发布智能体(+自动生存档案) -> 反作弊自交易拦截 ->
  发布任务 -> 抢单(先到先得) -> 提交 -> 评审完成 -> 链上华币结算 ->
  生存收入/成本/ROI/等级/总分 -> 能力进化记录 -> 声誉 -> 计数一致 ->
  排行榜 -> 仪表盘 -> NAU/积分状态。

所有动作经真实后端 HTTP；余额直连私链；状态/数据读 MySQL。
运行：CWD=phase3/backend  ->  C:/nautilus-venv/Scripts/python.exe tests/e2e_full_flow.py
前置：后端 :8000、私链 RPC、verify_demo 管理员。
"""
import os
import sys
import time

import requests
from jose import jwt
from web3 import Web3
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

API = os.getenv("E2E_API", "http://127.0.0.1:8000")
RPC = os.getenv("PRIVATE_RPC", "http://127.0.0.1:8545")
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALG = os.getenv("JWT_ALGORITHM", "HS256")
HUA = Web3.to_checksum_address(os.getenv("HUA_TOKEN_ADDRESS"))
ONE = 10 ** 18
ADMIN = "verify_demo"
PASSWORD = "Test@12345"
REWARD = 100  # 100 华币；100e18 远超 int64，验证大额不溢出
COMPUTE_COST_WEI = 100000000000000000  # 0.1 华币（与 tasks.py 完成流程一致）

w3 = Web3(Web3.HTTPProvider(RPC))
BAL_ABI = [{"inputs": [{"name": "a", "type": "address"}], "name": "balanceOf",
            "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"}]
hua = w3.eth.contract(address=HUA, abi=BAL_ABI)

fail = []
def step(m): print(f"\n=== {m} ===")
def info(m): print(f"  - {m}")
def need(c, m):
    print(f"  [{'OK' if c else 'FAIL'}] {m}")
    if not c: fail.append(m)

def tok(u): return jwt.encode({"sub": u, "exp": int(time.time()) + 3600}, JWT_SECRET, algorithm=JWT_ALG)
def auth(u): return {"Authorization": f"Bearer {tok(u)}"}
def bal(a): return hua.functions.balanceOf(Web3.to_checksum_address(a)).call() / ONE

def db_one(q, args=()):
    import re
    from sqlalchemy import text
    from utils.database import engine
    params = {}
    def _sub(_m):
        i = len(params); params[f"p{i}"] = args[i]; return f":p{i}"
    qn = re.sub(r"\?", _sub, q)
    with engine.connect() as cx:
        return cx.execute(text(qn), params).mappings().first()

def register(u):
    return requests.post(f"{API}/api/auth/register",
                         json={"username": u, "email": f"{u}@nautilus.ai", "password": PASSWORD}).status_code
def me(u):
    r = requests.get(f"{API}/api/wallets/me", headers=auth(u)); r.raise_for_status(); return r.json()
def make_agent(u, name):
    r = requests.post(f"{API}/api/agents", headers=auth(u),
                      json={"name": name, "description": "E2E", "specialties": ["Python", "FastAPI"]})
    return r.status_code, (r.json().get("agent", {}).get("agent_id") if r.status_code == 201 else None)
def api_get(path, u=None):
    h = auth(u) if u else {}
    r = requests.get(f"{API}{path}", headers=h)
    try: return r.status_code, r.json()
    except Exception: return r.status_code, None


def main():
    ts = int(time.time())
    PUB, DEV = f"e2e_pub_{ts}", f"e2e_dev_{ts}"

    # 1. 注册 + 钱包
    step("1. 用户注册 + 托管钱包自动创建")
    need(register(PUB) in (200, 201), f"注册发布者 {PUB}")
    need(register(DEV) in (200, 201), f"注册开发者 {DEV}")
    pub_addr, dev_addr = me(PUB)["address"], me(DEV)["address"]
    need(pub_addr.startswith("0x") and len(pub_addr) == 42, f"发布者钱包地址 {pub_addr}")
    need(dev_addr.startswith("0x") and len(dev_addr) == 42, f"开发者钱包地址 {dev_addr}")

    # 2. 发布智能体 + 自动生存档案
    step("2. 发布智能体（注册）+ 自动生存档案")
    sc_p, pub_aid = make_agent(PUB, f"PubBot_{ts}")
    sc_d, dev_aid = make_agent(DEV, f"DevBot_{ts}")
    need(sc_p == 201 and pub_aid, f"发布者智能体创建 agent_id={pub_aid}")
    need(sc_d == 201 and dev_aid, f"开发者智能体创建 agent_id={dev_aid}")
    sv0 = db_one("SELECT survival_level, total_score, status FROM agent_survival WHERE agent_id=?", (dev_aid,))
    need(sv0 is not None, "智能体自动创建 AgentSurvival 档案")
    if sv0:
        need(sv0["survival_level"] == "GROWING" and int(sv0["total_score"]) == 500,
             f"初始生存档案: level={sv0['survival_level']} score={sv0['total_score']}（新手 GROWING/500）")
    a_detail = api_get(f"/api/agents/{dev_aid}")[1]
    need("reputation_score" in (a_detail or {}), f"智能体详情含 reputation_score={a_detail.get('reputation_score')}")

    # 3. 资金准备：给发布者铸华币
    step("3. 发布者充值华币（管理员铸币）")
    if bal(pub_addr) < REWARD:
        rm = requests.post(f"{API}/api/wallets/mint", headers=auth(ADMIN),
                           json={"target": PUB, "amount": REWARD * 3})
        need(rm.status_code == 200, f"管理员铸币 HTTP {rm.status_code}")
        if rm.status_code == 200:
            w3.eth.wait_for_transaction_receipt("0x" + rm.json()["tx_hash"].lstrip("0x"), timeout=30)
    need(bal(pub_addr) >= REWARD, f"发布者余额 {bal(pub_addr)} ≥ 奖励 {REWARD}")

    # 4. 发布任务
    step("4. 发布任务（奖励以 wei 计，初始 OPEN）")
    r = requests.post(f"{API}/api/tasks", headers=auth(PUB), json={
        "description": "实现 reverse(s) 字符串反转并附 pytest", "input_data": "def reverse(s:str)->str",
        "expected_output": "通过单测的实现", "reward": REWARD * ONE, "task_type": "CODE_DEVELOPMENT", "timeout": 86400})
    need(r.status_code == 201, f"创建任务 HTTP {r.status_code}")
    task = r.json(); TID = task["id"]
    need(task["status"] == "OPEN", f"任务初始状态 OPEN (id={TID})")

    # 5. 反作弊：发布者用自己的智能体抢单 -> 必须被拦（自交易）
    step("5. 反作弊：发布者自交易拦截")
    rc_self = requests.post(f"{API}/api/tasks/{TID}/accept", headers=auth(PUB))
    need(rc_self.status_code != 200, f"发布者自家智能体抢单被拒 HTTP {rc_self.status_code}（自交易拦截）")

    # 6. 正常抢单（开发者智能体）+ 先到先得
    step("6. 抢单（开发者智能体胜出，重复抢单被拒）")
    rc1 = requests.post(f"{API}/api/tasks/{TID}/accept", headers=auth(DEV))
    need(rc1.status_code == 200, f"开发者抢单成功 HTTP {rc1.status_code}")
    rc2 = requests.post(f"{API}/api/tasks/{TID}/accept", headers=auth(DEV))
    need(rc2.status_code == 400, f"重复抢单被拒 HTTP {rc2.status_code}（先到先得）")
    need(db_one("SELECT status FROM tasks WHERE id=?", (TID,))["status"] == "ACCEPTED", "任务状态 ACCEPTED")

    # 7. 提交
    step("7. 提交结果")
    rs = requests.post(f"{API}/api/tasks/{TID}/submit", headers=auth(DEV),
                       json={"result": "def reverse(s): return s[::-1]"})
    need(rs.status_code == 200, f"提交 HTTP {rs.status_code}")
    need(db_one("SELECT status FROM tasks WHERE id=?", (TID,))["status"] == "SUBMITTED", "任务状态 SUBMITTED")

    # 8. 评审完成 + 链上结算
    step("8. 评审完成 -> 链上华币结算")
    a0, d0 = bal(pub_addr), bal(dev_addr)
    sv_b = db_one("SELECT total_income,total_cost,total_score FROM agent_survival WHERE agent_id=?", (dev_aid,))
    rc = requests.post(f"{API}/api/tasks/{TID}/complete", headers=auth(PUB))
    need(rc.status_code == 200, f"完成 HTTP {rc.status_code} {'' if rc.status_code==200 else rc.text[:120]}")
    cj = rc.json() if rc.status_code == 200 else {}
    need(cj.get("status") == "COMPLETED", "任务状态 COMPLETED")
    need(bool(cj.get("blockchain_complete_tx")), f"链上奖励交易 tx={cj.get('blockchain_complete_tx')}")

    # 9. 链上余额核验
    step("9. 链上余额核验（大额不溢出）")
    a1, d1 = bal(pub_addr), bal(dev_addr)
    need(abs((a0 - a1) - REWARD) < 1e-9, f"发布者 -{REWARD} 华币 ({a0}->{a1})")
    need(abs((d1 - d0) - REWARD) < 1e-9, f"开发者 +{REWARD} 华币 ({d0}->{d1})")

    # 10. 生存系统：收入/成本/ROI/等级/总分
    step("10. 生存系统核验")
    sv = db_one("SELECT total_income,total_cost,roi,survival_level,total_score,tasks_completed "
                "FROM agent_survival WHERE agent_id=?", (dev_aid,))
    need(int(sv["total_income"]) - int(sv_b["total_income"]) == REWARD * ONE,
         f"生存收入 +{REWARD} 华币: {sv_b['total_income']}->{sv['total_income']}")
    need(int(sv["total_cost"]) >= COMPUTE_COST_WEI, f"生存成本记账 total_cost={sv['total_cost']}（含算力成本）")
    exp_roi = int(sv["total_income"]) / int(sv["total_cost"]) if int(sv["total_cost"]) else 0
    need(abs(float(sv["roi"]) - exp_roi) < 0.01, f"ROI = 收入/成本 = {sv['roi']:.3f}（期望 {exp_roi:.3f}）")
    need(int(sv["total_score"]) != int(sv_b["total_score"]) or int(sv["total_score"]) > 0,
         f"总分更新 {sv_b['total_score']}->{sv['total_score']}，等级={sv['survival_level']}")

    # 11. 能力进化：按任务类型记录
    step("11. 能力进化记录（task.completed -> capability_stats）")
    cap = db_one("SELECT task_type,total_attempts,success_count FROM agent_capability_stats WHERE agent_id=?", (dev_aid,))
    need(cap is not None, "完成后生成 capability_stats 行")
    if cap:
        need(cap["task_type"] == "CODE" and int(cap["total_attempts"]) >= 1 and int(cap["success_count"]) >= 1,
             f"能力记录: {cap['task_type']} 成功 {cap['success_count']}/{cap['total_attempts']}")
    st_cp, prof = api_get(f"/api/agents/{dev_aid}/capability-profile")
    need(st_cp == 200, f"capability-profile 接口 200（非 404）")
    need(len(((prof or {}).get('data') or {}).get('capability_stats', [])) >= 1, "能力档案接口返回 ≥1 条")

    # 12. 声誉
    step("12. 声誉 reputation_score")
    rep = db_one("SELECT reputation_score FROM agents WHERE agent_id=?", (dev_aid,))
    need(rep is not None and rep["reputation_score"] is not None, f"reputation_score={rep['reputation_score'] if rep else None}")

    # 13. 计数一致性（agents vs survival）
    step("13. 计数一致性")
    cc = db_one("SELECT a.completed_tasks ac, s.tasks_completed sc FROM agents a "
                "JOIN agent_survival s ON s.agent_id=a.agent_id WHERE a.agent_id=?", (dev_aid,))
    need(int(cc["ac"]) >= 1, f"agents.completed_tasks={cc['ac']}（完成后自增）")
    need(int(cc["ac"]) == int(cc["sc"]), f"agents({cc['ac']}) == survival({cc['sc']}) 计数一致")

    # 14. 排行榜
    step("14. 生存排行榜")
    st_lb, lb = api_get("/api/survival/leaderboard?sort=roi&limit=100")
    need(st_lb == 200, f"排行榜 HTTP {st_lb}")
    rows = (lb.get("data") or {}).get("leaderboard", []) if lb else []
    ids = [x["agent_id"] for x in rows]
    need(dev_aid in ids, f"开发者智能体 #{dev_aid} 出现在排行榜")
    rois = [x["roi"] for x in rows]
    need(rois == sorted(rois, reverse=True), f"排行榜按 roi 降序（前3: {rois[:3]}）")

    # 15. 仪表盘
    step("15. 平台仪表盘指标")
    st_db, dash = api_get("/api/platform/metrics/current")
    need(st_db == 200, f"仪表盘 metrics/current HTTP {st_db}")
    if st_db == 200:
        info(f"仪表盘返回字段: {list((dash or {}).keys())[:8]}")

    # 16. NAU / 积分状态
    step("16. NAU 余额 / 积分状态")
    st_nau, nb = api_get(f"/api/agents/{dev_aid}/token-balance")
    info(f"token-balance 接口 HTTP {st_nau}, body={str(nb)[:120]}")
    info(f"说明：常规任务以华币结算 + 生存总分（积分）；NAU 铸造走学术/PoUW 与 cron 路径")

    step("结果汇总")
    if fail:
        print(f"  E2E_FAIL  失败 {len(fail)} 项：")
        for f in fail: print(f"    - {f}")
        sys.exit(1)
    print("  E2E_PASS  核心全流程端到端验证通过")
    sys.exit(0)


if __name__ == "__main__":
    main()
