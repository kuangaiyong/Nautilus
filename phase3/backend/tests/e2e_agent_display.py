"""E2E 回归：智能体展示契约（修复 NaN%/wei/ROI/能力统计/排行榜之后）。

覆盖本次修复的对外契约（真实后端 :8000，无 mock）：
  1. /api/agents/{id} 暴露 reputation_score + total_income，且 completed/failed 为整数
     （前端成功率依赖这些字段；字段缺失会算出 NaN%）。
  2. /api/agents/{id}/capability-profile 返回 200（此前 AgentCapabilityStat 重复定义 → 404）。
  3. /api/survival/leaderboard?sort=roi 真按 roi 降序（此前 sort 参数被忽略）。
  4. total_income 为可解析的 wei 整数串（前端 /1e18 展示华币）。

运行：CWD=phase3/backend  ->  C:/nautilus-venv/Scripts/python.exe tests/e2e_agent_display.py
依赖后端已在 127.0.0.1:8000 运行。
"""
import sys
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"


def api(path):
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, None


fails = []


def check(cond, msg):
    print(("  [OK] " if cond else "  [FAIL] ") + msg)
    if not cond:
        fails.append(msg)


def main():
    st, agents = api("/api/agents?limit=50")
    assert st == 200 and agents, f"agents list failed: {st}"
    # 优先选一个有真实收入的 agent，断言更强
    aid = agents[0]["agent_id"]
    for a in agents:
        if a.get("total_income") and a["total_income"] != "0":
            aid = a["agent_id"]
            break

    print(f"=== 1. /api/agents/{aid}: 字段契约 ===")
    st, a = api(f"/api/agents/{aid}")
    check(st == 200, f"GET /api/agents/{aid} -> 200")
    check("reputation_score" in a, "响应含 reputation_score")
    check(isinstance(a.get("reputation_score"), (int, float)), "reputation_score 为数值")
    check("total_income" in a, "响应含 total_income")
    check(isinstance(a.get("completed_tasks"), int) and isinstance(a.get("failed_tasks"), int),
          "completed_tasks/failed_tasks 为整数（前端成功率不再 NaN）")

    print(f"\n=== 2. /api/agents/{aid}/capability-profile: 不再 404 ===")
    st, prof = api(f"/api/agents/{aid}/capability-profile")
    check(st == 200, "capability-profile -> 200（此前重复表定义导致 404）")
    data = (prof or {}).get("data") or {}
    check("capability_stats" in data, "data 含 capability_stats 列表")

    print("\n=== 3. /api/survival/leaderboard?sort=roi: 排序生效 ===")
    st, lb = api("/api/survival/leaderboard?sort=roi")
    check(st == 200, "leaderboard -> 200")
    rows = (lb.get("data") or {}).get("leaderboard", [])
    rois = [r["roi"] for r in rows]
    check(rois == sorted(rois, reverse=True), f"按 roi 降序: {rois[:5]}")

    print("\n=== 4. wei->华币：total_income 可解析 ===")
    inc = str(a.get("total_income", "0"))
    check(inc.isdigit(), f"total_income 为 wei 整数串: {inc}")
    if inc.isdigit():
        print(f"       换算 = {int(inc) / 1e18} 华币")

    print("\n=== 结果 ===")
    if fails:
        print("E2E_FAIL:", fails)
        sys.exit(1)
    print("E2E_PASS 智能体展示契约全部通过")


if __name__ == "__main__":
    main()
