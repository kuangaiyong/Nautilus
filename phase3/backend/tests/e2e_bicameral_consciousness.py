"""二分心智展示内容 E2E（真实后端 + 真实 MySQL，无 mock）。

锁定两个已修复的 bug：
  1. 痛苦信号读遗留商业营收口径（Order/Customer，私有化后恒空），恒报
     「收入为零 / 没有付费客户」、痛苦指数恒 95%，与平台真实状态矛盾；
  2. reflect() 返回体引用 _derive_pain_signals 的局部变量（total_agents 等）
     → NameError → 端点 except 吞掉 → 前端恒显示 "Reflection unavailable."。

验证：
  - GET /api/dashboard/consciousness 返回真实 DMAS 指标（非降级文案）；
  - 指标与 DB 真值逐项一致（agents / agent_survival / tasks 直查）；
  - 痛苦信号只来自真实经济口径，绝无 revenue/customers/existential/conversion；
  - 痛苦信号与真实指标自洽，且连续多次抓取结果稳定（同一状态不漂移）。

运行:CWD=phase3/backend -> C:/nautilus-venv/Scripts/python.exe tests/e2e_bicameral_consciousness.py
前置:后端 127.0.0.1:8000 在线。
"""
import os
import sys

import requests
from dotenv import load_dotenv
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from utils.database import get_db_context  # noqa: E402

API = "http://127.0.0.1:8000"
LEGACY_SOURCES = {"revenue", "customers", "existential", "conversion"}

fail = []


def need(c, m):
    print(f"  [{'OK' if c else 'FAIL'}] {m}")
    if not c:
        fail.append(m)


def step(m):
    print(f"\n=== {m} ===")


def fetch():
    r = requests.get(f"{API}/api/dashboard/consciousness", timeout=60)
    r.raise_for_status()
    return r.json()


def db_truth():
    """直查 DB 拿真值，用于交叉核对接口指标（口径同 api.platform._compute_metrics）。"""
    with get_db_context() as db:
        total_agents = db.execute(text("SELECT COUNT(*) FROM agents")).scalar()
        healthy = db.execute(text(
            "SELECT COUNT(*) FROM agent_survival "
            "WHERE survival_level IN ('ELITE','MATURE','GROWING')"
        )).scalar()
        done_24h = db.execute(text(
            "SELECT COUNT(*) FROM tasks WHERE status='COMPLETED' "
            "AND created_at >= (NOW() - INTERVAL 24 HOUR)"
        )).scalar()
        total_tasks = db.execute(text("SELECT COUNT(*) FROM tasks")).scalar()
        # 近 24h 真实结算数（market 痛苦的判定口径）
        settled_24h = db.execute(text(
            "SELECT COUNT(*) FROM tasks WHERE "
            "(status='COMPLETED' AND completed_at >= (NOW() - INTERVAL 24 HOUR)) "
            "OR (status='FAILED' AND verified_at >= (NOW() - INTERVAL 24 HOUR))"
        )).scalar()
        # 遗留商业口径应恒空（私有化平台无订单/客户）
        orders = db.execute(text("SELECT COUNT(*) FROM orders")).scalar()
        customers = db.execute(text("SELECT COUNT(*) FROM customers")).scalar()
    return {"total_agents": total_agents, "agents_healthy": healthy,
            "tasks_completed_24h": done_24h, "total_tasks": total_tasks,
            "settled_24h": settled_24h, "orders": orders, "customers": customers}


def main():
    step("1. 调用真实接口 GET /api/dashboard/consciousness")
    d = fetch()
    need("error" not in d, f"端点未降级（无 error 字段），实际 error={d.get('error')!r}")
    need(d.get("report_text") not in (None, "", "Reflection unavailable."),
         "report_text 是真实反思报告，而非 'Reflection unavailable.'")
    m = d.get("metrics") or {}
    need(bool(m), f"metrics 非空: {m}")

    step("2. 接口指标 vs DB 真值 逐项交叉核对")
    t = db_truth()
    print(f"  DB 真值: {t}")
    print(f"  接口指标: {m}")
    for k in ("total_agents", "agents_healthy", "tasks_completed_24h", "total_tasks"):
        need(m.get(k) == t[k], f"{k}: 接口={m.get(k)} == DB={t[k]}")
    # total_tasks 是 SelfImprovementEngine 生成 platform_evolution 任务的闸门，不能丢
    need(m.get("total_tasks", 0) > 0, "metrics.total_tasks 存在且 >0（自举任务生成闸门未被关掉）")

    step("3. 遗留商业口径已彻底剥离")
    need(t["orders"] == 0 and t["customers"] == 0,
         f"DB 中 orders={t['orders']} customers={t['customers']}（私有化平台恒空）")
    sources = {p["source"] for p in d.get("pain_signals", [])}
    print(f"  痛苦信号来源: {sorted(sources) or '（无）'}")
    need(sources.isdisjoint(LEGACY_SOURCES),
         f"痛苦信号不含遗留口径 {sorted(LEGACY_SOURCES)}")

    step("4. 痛苦信号与真实指标自洽")
    if m["agents_healthy"] == m["total_agents"]:
        need("survival" not in sources, "全部智能体健康 → 无 survival 痛苦")
    else:
        need("survival" in sources, "有智能体濒危 → 必有 survival 痛苦")
    # market 痛苦按「近 24h 真实结算数」判定（completed_at/verified_at），
    # 而非 created_at 窗口的 tasks_completed_24h——否则旧任务刚结算会误报停摆
    if t["settled_24h"] == 0:
        need("market" in sources, "近 24h 零任务结算 → 必有 market 痛苦")
    else:
        need("market" not in sources, "近 24h 有任务结算 → 无 market 痛苦")

    # quality 痛苦必须带真实的 worst_type/fail_count，否则自举模板会发出
    # 「修复 unknown 任务类型的失败模式」这种垃圾任务
    for p in d.get("pain_signals", []):
        if p["source"] == "quality":
            need(p.get("worst_type") and p.get("fail_count", 0) > 0,
                 f"quality 痛苦携带真实 worst_type/fail_count: {p}")

    intensities = [p["intensity"] for p in d.get("pain_signals", [])]
    need(intensities == sorted(intensities, reverse=True), "痛苦信号按强度降序")
    if intensities:
        avg = sum(intensities) / len(intensities)
        print(f"  痛苦指数: {avg * 100:.0f}%")
        need(avg < 0.95, f"痛苦指数 {avg * 100:.0f}% 不再是遗留 bug 的恒定 95%")

    step("5. 同一平台状态下，连续抓取结果稳定")
    def key(x):
        return (x.get("metrics"), x.get("pain_signals"), x.get("dialogue"))
    d2, d3 = fetch(), fetch()
    need(key(d) == key(d2) == key(d3), "连续 3 次抓取 metrics/pain_signals/dialogue 完全一致")

    step("结果")
    if fail:
        print(f"FAILED ({len(fail)}):")
        for f in fail:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
