"""私链管理台的真实端到端验证（真链、真接口、无 mock）。

全程：起链 → 后端真实华币结算 → 加节点 → clique 投票升签名者 → 真实压到 quorum 边缘
验证护栏 → 罢免 → 删节点 → 恢复满编。

第 6 步会真的把链压到出块下限，这是「护栏有效」唯一诚实的证明方式；收尾无条件跑一次
幂等的 /start 把链恢复满编，即使中途断言失败也会执行。

前置：后端跑在 API（私链可以是停的，本脚本会把它起起来）。
跑法：C:/nautilus-venv/Scripts/python.exe tests/e2e_chain_admin.py
"""
import os
import secrets
import sys
import time

import requests
from dotenv import load_dotenv
from web3 import Web3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

import models.agent_survival  # noqa: F401  注册 mapper（Agent 跨模块 relationship）
from models.database import User
from utils.database import SessionLocal

API = os.getenv("E2E_API", "http://127.0.0.1:8000")
RPC = os.getenv("PRIVATE_RPC", "http://127.0.0.1:8545")
CHAIN_ROOT = os.getenv("CHAIN_ROOT", r"C:\nautilus-privatechain")
HUA = Web3.to_checksum_address(os.getenv("HUA_TOKEN_ADDRESS"))
BASE = f"{API}/api/admin/chain"

BAL_ABI = [{"inputs": [{"name": "a", "type": "address"}], "name": "balanceOf",
            "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view",
            "type": "function"}]

CHECKS = []
H = {}


def check(name, ok, extra=""):
    CHECKS.append(ok)
    print(f"  {'[OK]  ' if ok else '[FAIL]'} {name}" + (f"   {extra}" if extra else ""))
    return ok


def reg_login(username, password="Verify@12345"):
    requests.post(f"{API}/api/auth/register", timeout=20,
                  json={"username": username, "email": f"{username}@nautilus-corp.com",
                        "password": password})
    r = requests.post(f"{API}/api/auth/login", timeout=20,
                      json={"username": username, "password": password})
    r.raise_for_status()
    d = r.json()
    return d.get("access_token") or d.get("data", {}).get("access_token")


def promote_admin(username):
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.username == username).first()
        u.is_admin = True
        db.commit()
    finally:
        db.close()


def call(method, path, body=None):
    return requests.request(method, f"{BASE}{path}", headers=H, json=body, timeout=30)


def status():
    r = call("GET", "/status")
    r.raise_for_status()
    return r.json()


def raw_rpc(port, method, params):
    """直连某个节点的 geth RPC（绕开后端），用来构造测试前置条件。"""
    r = requests.post(f"http://127.0.0.1:{port}", timeout=10,
                      json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    return r.json().get("result")


def wait_op(resp, timeout=300):
    assert resp.status_code == 202, f"期望 202，实际 {resp.status_code}: {resp.text[:300]}"
    op_id = resp.json()["op_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        op = requests.get(f"{BASE}/operations/{op_id}", headers=H, timeout=15).json()
        if op["state"] != "running":
            for line in op["log"]:
                print(f"        {line}")
            assert op["state"] == "done", f"操作失败：{op.get('error')}"
            return op
        time.sleep(2)
    raise TimeoutError(f"操作 {op_id} 超过 {timeout}s 未结束")


def wait_for(desc, pred, timeout=90):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = status()
        if pred(last):
            return last
        time.sleep(2)
    raise AssertionError(f"等待超时：{desc}\n  最后状态：producing={last['producing']} "
                         f"signers={len(last['signers'])} online={last['online_signers']} "
                         f"peers={[(n['index'], n['peer_count']) for n in last['nodes']]}")


def all_peered(st):
    """每个在线节点都连上了其余所有在线节点。"""
    live = [n for n in st["nodes"] if n["running"]]
    return len(live) == len(st["nodes"]) and all(n["peer_count"] == len(live) - 1 for n in live)


def main():
    stamp = secrets.token_hex(3)
    admin_user, plain_user = f"e2e_chadmin_{stamp}", f"e2e_chuser_{stamp}"

    admin_token = reg_login(admin_user)
    plain_token = reg_login(plain_user)
    promote_admin(admin_user)
    H["Authorization"] = f"Bearer {admin_token}"

    print("\n[1] 鉴权")
    r = requests.get(f"{BASE}/status", timeout=20,
                     headers={"Authorization": f"Bearer {plain_token}"})
    check("非管理员访问 /status 被拒", r.status_code == 403, f"HTTP {r.status_code}")
    check("管理员可读 /status", status() is not None)

    print("\n[2] 启动私链（当前应为停止态）")
    wait_op(call("POST", "/start"))
    st = wait_for("5 节点全部启动、两两互联、开始出块",
                  lambda s: all_peered(s) and s["producing"] and s["online_signers"] == 5)
    node_count = len(st["nodes"])
    check("全部节点在线", all(n["running"] for n in st["nodes"]), f"{node_count} 个")
    check("两两互联（每个节点 peers = N-1）", all_peered(st),
          f"peers={[n['peer_count'] for n in st['nodes']]}")
    check("链正在出块", st["producing"], f"块高 {st['latest_block']}，上个块 {st['seconds_since_block']}s 前")
    check("在线签名者 5/5，出块下限 3", st["online_signers"] == 5 and st["quorum"] == 3)
    check("无孤立节点", not any(n["isolated"] for n in st["nodes"]))

    print("\n[3] 后端在这条新起的链上做真实华币结算")
    w3 = Web3(Web3.HTTPProvider(RPC))
    hua = w3.eth.contract(address=HUA, abi=BAL_ABI)
    target = "0x" + secrets.token_hex(20)
    before = hua.functions.balanceOf(Web3.to_checksum_address(target)).call()
    r = requests.post(f"{API}/api/wallets/mint", headers=H, timeout=60,
                      json={"target": target, "amount": 7})
    tx = r.json().get("tx_hash", "") if r.ok else ""
    rcpt = w3.eth.wait_for_transaction_receipt("0x" + tx.lstrip("0x"), timeout=60) if tx else None
    after = hua.functions.balanceOf(Web3.to_checksum_address(target)).call()
    check("华币铸币上链成功（证明后端结算在新链上可用）",
          r.ok and rcpt is not None and rcpt.status == 1 and after == before + 7 * 10 ** 18,
          f"HTTP {r.status_code}, 余额 {before / 10**18} → {after / 10**18} HUA")

    print("\n[4] 新增一个非签名节点")
    before_ids = {n["index"] for n in status()["nodes"]}
    wait_op(call("POST", "/nodes", {"make_signer": False}))
    st = status()
    new_ids = {n["index"] for n in st["nodes"]} - before_ids
    check("注册表里多了一个节点", len(new_ids) == 1, f"新节点 index={new_ids}")
    idx = new_ids.pop()
    new_node = next(n for n in st["nodes"] if n["index"] == idx)
    st = wait_for(f"node{idx} 起来并与其余 5 个节点互联",
                  lambda s: all_peered(s) and len(s["nodes"]) == node_count + 1)
    new_node = next(n for n in st["nodes"] if n["index"] == idx)
    check(f"node{idx} 运行中且已同步", new_node["running"] and not new_node["isolated"])
    check(f"node{idx} peers = 5，其余节点 peers 也升到 5", all_peered(st),
          f"peers={[n['peer_count'] for n in st['nodes']]}")
    check(f"node{idx} 默认不是签名者（对共识零影响）", not new_node["is_signer"])
    check("签名者仍是 5 个，出块下限仍是 3", len(st["signers"]) == 5 and st["quorum"] == 3)
    check("链仍在出块", st["producing"])

    print(f"\n[5] 投票把 node{idx} 选为签名者（clique，需等出块生效）")
    wait_op(call("POST", "/signers", {"address": new_node["signer"], "authorize": True}))
    st = wait_for(f"node{idx} 进入签名者集合",
                  lambda s: new_node["signer"] in s["signers"], timeout=150)
    check(f"node{idx} 已成为签名者", new_node["signer"] in st["signers"])
    check("签名者 5 → 6，出块下限自动 3 → 4（未做成配置项）",
          len(st["signers"]) == 6 and st["quorum"] == 4,
          f"signers={len(st['signers'])} quorum={st['quorum']}")
    check("链仍在出块", st["producing"])

    print("\n[6] 真实压到 quorum 边缘，验证硬护栏")
    # 6 个签名者、下限 4。停 2 个还剩 4 == 下限（放行）；再停第 3 个会跌到 3 < 4（必须拒绝）。
    wait_op(call("POST", "/nodes/5/stop"))
    wait_op(call("POST", "/nodes/4/stop"))
    st = wait_for("在线签名者降到 4（正好等于出块下限）",
                  lambda s: s["online_signers"] == 4)
    check("停到下限仍放行，链继续出块", st["producing"] and st["online_signers"] == st["quorum"],
          f"online={st['online_signers']} quorum={st['quorum']}")

    r = call("POST", "/nodes/3/stop")
    body = r.json()
    check("再停一个会跌破下限 → 409 QUORUM_WOULD_BREAK",
          r.status_code == 409 and body["detail"]["error"]["code"] == "QUORUM_WOULD_BREAK",
          f"HTTP {r.status_code} {body.get('detail', {}).get('error', {}).get('code')}")
    check("被拒后链依然在出块（没被误伤）", status()["producing"])

    r = call("POST", "/nodes/1/stop")
    check("单独停 RPC 入口节点 → 409 RPC_ENTRY_NODE",
          r.status_code == 409 and r.json()["detail"]["error"]["code"] == "RPC_ENTRY_NODE",
          f"HTTP {r.status_code}")

    print("\n[7] 用单节点启动端点恢复（顺带验证它会强制重新组网）")
    wait_op(call("POST", "/nodes/4/start"))
    wait_op(call("POST", "/nodes/5/start"))
    st = wait_for("6 个节点全部回来且两两互联", lambda s: all_peered(s) and s["online_signers"] == 6)
    check("单节点启动后自动重新组网（没变成 0 peer 的孤岛）",
          all_peered(st) and not any(n["isolated"] for n in st["nodes"]),
          f"peers={[n['peer_count'] for n in st['nodes']]}")

    print(f"\n[8] 罢免 node{idx} 的签名者身份")
    r = call("DELETE", f"/nodes/{idx}")
    check("还是签名者时删节点 → 409 STILL_A_SIGNER",
          r.status_code == 409 and r.json()["detail"]["error"]["code"] == "STILL_A_SIGNER",
          f"HTTP {r.status_code}")

    wait_op(call("POST", "/signers", {"address": new_node["signer"], "authorize": False}))
    st = wait_for(f"node{idx} 移出签名者集合",
                  lambda s: new_node["signer"] not in s["signers"], timeout=150)
    check(f"node{idx} 已被罢免", new_node["signer"] not in st["signers"])
    check("签名者 6 → 5，出块下限 4 → 3", len(st["signers"]) == 5 and st["quorum"] == 3)

    print(f"\n[9] 删除 node{idx}")
    datadir = os.path.join(CHAIN_ROOT, new_node["datadir"])
    wait_op(call("DELETE", f"/nodes/{idx}"))
    st = wait_for("其余 5 个节点 peers 回到 4",
                  lambda s: len(s["nodes"]) == node_count and all_peered(s))
    check(f"node{idx} 已移出注册表", idx not in {n['index'] for n in st["nodes"]})
    check("其余节点 peers 回到 4（peer 已被摘除）", all_peered(st),
          f"peers={[n['peer_count'] for n in st['nodes']]}")
    check("datadir 保留在磁盘上（可加回恢复，不删数据）", os.path.isdir(datadir), datadir)
    check("链仍在出块", st["producing"])

    print("\n[10] 撤销一个进行中的签名者提案")
    ghost = "0x" + secrets.token_hex(20)
    entry = next(n for n in status()["nodes"] if n["is_rpc_entry"])
    # 只在一个节点上投票：5 个签名者要 3 票才生效，1 票永远落不了地 → 提案会一直挂着，
    # 这样撤销就能被确定性地验证，不依赖跟出块抢时间的竞态。
    raw_rpc(entry["http"], "clique_propose", [ghost, True])
    st = wait_for("提案挂到 /status 的 proposals 上",
                  lambda s: ghost in {k.lower() for k in s["proposals"]}, timeout=30)
    check("提案处于挂起状态", ghost in {k.lower() for k in st["proposals"]})
    wait_op(call("DELETE", f"/signers/proposals/{ghost}"))
    st = wait_for("提案已消失",
                  lambda s: ghost not in {k.lower() for k in s["proposals"]}, timeout=30)
    check("撤销后提案从 proposals 中移除", ghost not in {k.lower() for k in st["proposals"]})
    check("该地址自始至终没成为签名者", ghost not in {a.lower() for a in st["signers"]})

    print("\n[11] 新增节点并一步到位选为签名者（前端「新增节点（出块）」走这条）")
    before_ids = {n["index"] for n in status()["nodes"]}
    wait_op(call("POST", "/nodes", {"make_signer": True}))
    st = status()
    idx2 = ({n["index"] for n in st["nodes"]} - before_ids).pop()
    born = next(n for n in st["nodes"] if n["index"] == idx2)
    check(f"node{idx2} 加入后直接成为签名者", born["is_signer"] and born["running"])
    check("签名者 5 → 6，出块下限 3 → 4", len(st["signers"]) == 6 and st["quorum"] == 4,
          f"signers={len(st['signers'])} quorum={st['quorum']}")
    check("链仍在出块", st["producing"])

    # 清理：罢免 + 删除，恢复成 5 节点
    wait_op(call("POST", "/signers", {"address": born["signer"], "authorize": False}))
    wait_for(f"node{idx2} 移出签名者集合",
             lambda s: born["signer"] not in s["signers"], timeout=150)
    wait_op(call("DELETE", f"/nodes/{idx2}"))
    st = wait_for("回到 5 节点满编", lambda s: len(s["nodes"]) == node_count and all_peered(s))
    check("已清理回 5 节点", len(st["nodes"]) == node_count and st["producing"])

    print("\n[12] 停止整条链（唯一能带走 RPC 入口节点的操作）")
    check("不带 confirm → 422", call("POST", "/stop", {}).status_code == 422)
    check("confirm 值不对 → 422", call("POST", "/stop", {"confirm": "yes"}).status_code == 422)
    wait_op(call("POST", "/stop", {"confirm": "STOP"}))
    st = wait_for("所有节点停止", lambda s: not s["running"], timeout=60)
    check("整条链已停止（含 RPC 入口节点 node1）",
          not st["running"] and not any(n["running"] for n in st["nodes"]))


if __name__ == "__main__":
    failed = False
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        failed = True
        print(f"\n[异常] {type(exc).__name__}: {exc}")
    finally:
        print("\n[收尾] 无条件恢复私链满编")
        try:
            wait_op(call("POST", "/start"))
            final = status()
            print(f"        节点 {len(final['nodes'])} 个，在线签名者 "
                  f"{final['online_signers']}/{len(final['signers'])}，"
                  f"出块中={final['producing']}，块高 {final['latest_block']}")
        except Exception as exc:  # noqa: BLE001
            failed = True
            print(f"        [收尾失败] {exc}")

    ok = bool(CHECKS) and all(CHECKS) and not failed
    print(f"\n通过 {sum(CHECKS)}/{len(CHECKS)} 项断言")
    print("E2E_CHAIN_ADMIN_PASS" if ok else "E2E_CHAIN_ADMIN_FAIL")
    sys.exit(0 if ok else 1)
