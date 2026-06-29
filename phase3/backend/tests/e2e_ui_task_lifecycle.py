"""
任务全生命周期 —— 界面（浏览器）端到端自动化测试，无 mock。

用 Playwright 真实驱动前端页面，完整走一遍核心业务流程：

    alice 打开 /login 用账号密码登录
      -> /tasks/create 表单发布开发任务
      -> 智能体主人(e2e_dev_a)另开浏览器上下文登录
      -> 在任务详情页点击「接受任务」抢单
         （其余智能体 e2e_dev_b 打开页面看不到「接受任务」按钮，证明已被抢）
      -> 点击「提交结果」填写交付物提交
      -> alice 回到任务详情页点击「评审通过并发放奖励」
      -> 页面状态流转 开放中 -> 已接单 -> 已提交 -> 已完成
      -> 链上华币奖励真实结算（直连私链核验余额转移守恒）

页面动作全部经真实浏览器 UI；账号/智能体等前置条件经真实后端 API 准备；
余额由直连私链核验。运行：

    C:/nautilus-venv/Scripts/python.exe tests/e2e_ui_task_lifecycle.py

前置：后端 127.0.0.1:8000、私链 8545、前端 dev server localhost:3000 均在线，
verify_demo 为管理员。注意页面源必须用 localhost:3000（后端 CORS 仅放行它）。
"""
import os
import re
import sys
import time

import requests
from web3 import Web3
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, expect, TimeoutError as PWTimeout

load_dotenv()

BASE = os.getenv("E2E_UI_BASE", "http://localhost:3000")   # 必须 localhost，CORS 仅放行它
API = os.getenv("E2E_API", "http://127.0.0.1:8000")
RPC = os.getenv("PRIVATE_RPC", "http://127.0.0.1:8545")
HUA = Web3.to_checksum_address(os.getenv("HUA_TOKEN_ADDRESS"))
ONE = 10 ** 18

PUBLISHER = "alice"
OWNER = "e2e_dev_a"        # 抢到任务并实现的智能体主人
RIVAL = "e2e_dev_b"        # 竞争者：被抢后页面应看不到「接受任务」
PASSWORD = "Test@12345"
ADMIN = "verify_demo"
ADMIN_PASSWORD = "Verify@12345"
REWARD_HUA = 50            # 50 华币

w3 = Web3(Web3.HTTPProvider(RPC))
BAL_ABI = [{"inputs": [{"name": "a", "type": "address"}], "name": "balanceOf",
            "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"}]
hua = w3.eth.contract(address=HUA, abi=BAL_ABI)

fail = []


def step(m): print(f"\n=== {m} ===", flush=True)
def info(m): print(f"  - {m}", flush=True)
def need(cond, m):
    print(f"  [{'OK' if cond else 'FAIL'}] {m}", flush=True)
    if not cond:
        fail.append(m)


def bal(addr):
    return hua.functions.balanceOf(Web3.to_checksum_address(addr)).call()


# ---------------- 真实后端 API：仅用于准备前置条件（账号/智能体/充值）----------------
def api_register(username, password):
    return requests.post(f"{API}/api/auth/register",
                         json={"username": username, "email": f"{username}@nautilus-corp.com",
                               "password": password}).status_code


def api_login(username, password):
    r = requests.post(f"{API}/api/auth/login", json={"username": username, "password": password})
    r.raise_for_status()
    return r.json()["access_token"]


def api_me(token):
    r = requests.get(f"{API}/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    return r.json()["data"]["user"]


def api_ensure_agent(token, name):
    """确保该主人名下有智能体（抢单前置）。已存在则忽略错误。"""
    r = requests.post(f"{API}/api/agents", headers={"Authorization": f"Bearer {token}"},
                      json={"name": name, "description": "UI E2E 开发智能体",
                            "specialties": ["Python", "FastAPI"]})
    return r.status_code


def api_mint(admin_token, target_username, amount_hua):
    r = requests.post(f"{API}/api/wallets/mint", headers={"Authorization": f"Bearer {admin_token}"},
                      json={"target": target_username, "amount": amount_hua})
    r.raise_for_status()
    return r.json()["tx_hash"]


# ---------------- Playwright 页面动作：核心流程全部走真实 UI ----------------
def ui_login(page, username, password):
    page.goto(f"{BASE}/login")
    page.get_by_text("中国大陆用户").click()
    page.get_by_placeholder("用户名 / Username").fill(username)
    page.get_by_placeholder("密码 / Password").fill(password)
    page.get_by_role("button", name="账号密码登录").click()
    # 登录成功后 Login.tsx 执行 window.location.href='/'
    page.wait_for_url(f"{BASE}/", timeout=20000)
    # 等 AuthProvider 调 /auth/me 把 token 落到 localStorage（受保护页据此放行）
    page.wait_for_function("() => !!localStorage.getItem('nautilus_auth_token')", timeout=10000)


def ui_publish(page, description, requirements, reward_hua):
    page.goto(f"{BASE}/tasks/create")
    page.wait_for_selector("textarea[name=description]", timeout=20000)
    page.fill("textarea[name=description]", description)
    page.fill("textarea[name=requirements]", requirements)
    page.fill("input[name=reward]", str(reward_hua))
    page.get_by_role("button", name="发布任务").click()
    # 发布成功后展示成功页，2 秒后跳转 /tasks/{id}
    page.wait_for_url(re.compile(r"/tasks/\d+$"), timeout=25000)
    return int(page.url.rstrip("/").split("/")[-1])


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # ---- 0. 前置条件（真实 API 准备账号 / 智能体 / 余额）----
        step("0. 准备参与者（发布者 alice + 抢单智能体主人 + 竞争者）")
        for u in (OWNER, RIVAL):
            info(f"{u}: register={api_register(u, PASSWORD)}")
        for u, name in ((OWNER, "DevBot_A_UI"), (RIVAL, "DevBot_B_UI")):
            tk = api_login(u, PASSWORD)
            info(f"{u}: ensure_agent={api_ensure_agent(tk, name)} wallet={api_me(tk)['wallet_address']}")

        pub_token = api_login(PUBLISHER, PASSWORD)
        pub_addr = api_me(pub_token)["wallet_address"]
        win_addr = api_me(api_login(OWNER, PASSWORD))["wallet_address"]
        info(f"alice(发布者) wallet={pub_addr}")
        info(f"{OWNER}(抢单者) wallet={win_addr}")

        if bal(pub_addr) < REWARD_HUA * ONE:
            admin_token = api_login(ADMIN, ADMIN_PASSWORD)
            txh = api_mint(admin_token, PUBLISHER, REWARD_HUA * 5)
            w3.eth.wait_for_transaction_receipt("0x" + txh.lstrip("0x"), timeout=30)
        need(bal(pub_addr) >= REWARD_HUA * ONE, f"alice 华币余额 {bal(pub_addr)/ONE} ≥ 奖励 {REWARD_HUA}")

        ctx_pub = browser.new_context()
        ctx_dev = browser.new_context()
        ctx_rival = browser.new_context()
        page_pub = ctx_pub.new_page()
        page_dev = ctx_dev.new_page()
        page_rival = ctx_rival.new_page()
        for pg, who in ((page_pub, "alice"), (page_dev, OWNER), (page_rival, RIVAL)):
            pg.on("pageerror", lambda e, who=who: print(f"  [pageerror:{who}] {e}", flush=True))

        # ---- 1. alice 浏览器登录并发布任务 ----
        step("1. alice 登录前端并发布开发任务")
        ui_login(page_pub, PUBLISHER, PASSWORD)
        info("alice 登录成功，已跳转首页")
        tid = ui_publish(
            page_pub,
            "实现一个字符串反转函数 reverse(s)，并附带 pytest 单测",
            "函数签名 def reverse(s: str) -> str；需通过 reverse('abc')=='cba' 等用例",
            REWARD_HUA,
        )
        info(f"发布成功，任务详情页 task.id={tid}")
        expect(page_pub.get_by_text("开放中")).to_be_visible(timeout=10000)
        need(True, f"任务发布并进入详情页（状态=开放中），task.id={tid}")

        # ---- 2. 智能体主人登录抢单 ----
        step("2. 智能体主人 e2e_dev_a 登录并「接受任务」抢单")
        ui_login(page_dev, OWNER, PASSWORD)
        page_dev.goto(f"{BASE}/tasks/{tid}")
        accept_btn = page_dev.get_by_role("button", name="接受任务")
        accept_btn.wait_for(timeout=20000)
        accept_btn.click()
        expect(page_dev.get_by_text("已接单")).to_be_visible(timeout=20000)
        need(True, "e2e_dev_a 在 UI 上成功抢单（状态=已接单）")

        # ---- 3. 竞争者看不到「接受任务」（已被抢，先到先得）----
        step("3. 竞争者 e2e_dev_b 打开任务页应无法再抢")
        ui_login(page_rival, RIVAL, PASSWORD)
        page_rival.goto(f"{BASE}/tasks/{tid}")
        expect(page_rival.get_by_text("已接单")).to_be_visible(timeout=20000)
        page_rival.wait_for_timeout(1500)  # 给 user 注入与重渲染留时间
        rival_can_accept = page_rival.get_by_role("button", name="接受任务").count()
        need(rival_can_accept == 0, "竞争者页面已无「接受任务」按钮（任务已被抢）")

        # ---- 4. 抢到的智能体实现并提交 ----
        step("4. e2e_dev_a 在 UI 上「提交结果」")
        submit_open = page_dev.get_by_role("button", name="提交结果")
        submit_open.wait_for(timeout=20000)
        submit_open.click()
        deliverable = ("def reverse(s: str) -> str:\n    return s[::-1]\n\n"
                       "# tests\nassert reverse('abc') == 'cba'\nassert reverse('') == ''")
        page_dev.fill("textarea[placeholder='请输入你的结果…']", deliverable)
        page_dev.get_by_role("button", name="提交", exact=True).click()
        expect(page_dev.get_by_text("已提交")).to_be_visible(timeout=20000)
        need(True, "e2e_dev_a 提交结果成功（状态=已提交）")

        # ---- 5. 发布者评审通过 -> 链上华币奖励结算 ----
        step("5. alice 评审通过并发放奖励（链上结算）")
        a0, w0 = bal(pub_addr), bal(win_addr)
        info(f"结算前: alice={a0/ONE} HUA, {OWNER}={w0/ONE} HUA")
        page_pub.goto(f"{BASE}/tasks/{tid}")
        complete_btn = page_pub.get_by_role("button", name="评审通过并发放奖励")
        complete_btn.wait_for(timeout=20000)
        complete_btn.click()
        expect(page_pub.get_by_text("已完成")).to_be_visible(timeout=30000)
        need(True, "alice 在 UI 上评审通过，任务状态=已完成")

        # ---- 6. 链上余额核验（奖励真实转账，守恒）----
        step("6. 链上余额核验")
        a1, w1 = bal(pub_addr), bal(win_addr)
        info(f"结算后: alice={a1/ONE} HUA, {OWNER}={w1/ONE} HUA")
        moved = w1 - w0
        need(moved == (a0 - a1), f"华币守恒：alice 扣减 == 智能体到账 == {moved/ONE} HUA")
        need(moved > 0, f"智能体真实收到奖励 {moved/ONE} HUA")
        need(abs(moved / ONE - REWARD_HUA) < 1e-3, f"到账金额≈发布奖励 {REWARD_HUA} HUA（实际 {moved/ONE}）")

        browser.close()

    # ---- 汇总 ----
    step("结果汇总")
    if fail:
        print(f"  UI_E2E_FAIL  失败 {len(fail)} 项：", flush=True)
        for f in fail:
            print(f"    - {f}", flush=True)
        sys.exit(1)
    print("  UI_E2E_PASS  界面全流程端到端验证通过"
          "（登录→发布→抢单→提交→评审→链上奖励）", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
