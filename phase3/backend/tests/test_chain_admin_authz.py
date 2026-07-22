"""私链管理台的鉴权与护栏。

护栏（precheck）真跑，但所有 do_* 都被打成 no-op —— 这些用例绝不能真去起停 geth。
真实的启停/增删/投票由 tests/e2e_chain_admin.py 打真链验证。
"""
import os
import socket
import sys
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.database import Base, User
from utils.database import get_db
from utils.auth import hash_password, create_access_token
from api.chain_admin import router as chain_router
from services import chain_manager as cm
from tests.testdb import TEST_DATABASE_URL

engine = create_engine(TEST_DATABASE_URL)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 5 个假签名者地址，够区分即可
_SIGNERS = [f"0x{i:040x}" for i in range(1, 7)]


def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def _status(running=(1, 2, 3, 4, 5), signers=(1, 2, 3, 4, 5), peers=4, producing=True):
    """造一个 get_chain_status() 形状的快照。running/signers 是节点序号集合。"""
    nodes = []
    for i in range(1, 6):
        up = i in running
        nodes.append({
            "index": i, "datadir": "data" if i == 1 else f"node{i}",
            "p2p": 30302 + i, "http": 8545 if i == 1 else 8545 + i,
            "ws": 8546 if i == 1 else None, "authrpc": 8550 + i,
            "signer": _SIGNERS[i - 1],
            "pid": 1000 + i if up else None,
            "running": up,
            "block_number": 100 if up else None,
            "peer_count": peers if up else None,
            "mining": up, "enode": f"enode://{i}@127.0.0.1:3030{i}" if up else None,
            "isolated": up and peers == 0,
            "is_signer": i in signers,
            "is_rpc_entry": i == 1,
        })
    signer_addrs = [_SIGNERS[i - 1] for i in signers]
    quorum = len(signer_addrs) // 2 + 1 if signer_addrs else 0
    online = sum(1 for n in nodes
                 if n["is_signer"] and n["running"] and (n["peer_count"] or 0) > 0)
    return {
        "running": any(n["running"] for n in nodes), "producing": producing,
        "latest_block": 100, "seconds_since_block": 2,
        "signers": signer_addrs, "quorum": quorum, "online_signers": online,
        "proposals": {}, "rpc_entry_port": 8545, "nodes": nodes,
    }


def _registry():
    return {"network_id": 13370, "nodes": [
        {"index": n["index"], "datadir": n["datadir"], "p2p": n["p2p"], "http": n["http"],
         "ws": n["ws"], "authrpc": n["authrpc"], "signer": n["signer"]}
        for n in _status()["nodes"]
    ]}


@pytest.fixture
def client(monkeypatch):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    # 所有真干活的函数都断掉：这些用例只验证鉴权与护栏，绝不碰真实 geth 进程
    for name in ("do_start_chain", "do_stop_chain", "do_start_node", "do_stop_node",
                 "do_add_node", "do_remove_node", "do_propose_signer", "do_discard_proposal"):
        monkeypatch.setattr(cm, name, lambda *a, **k: None)
    monkeypatch.setattr(cm, "load_registry", _registry)
    monkeypatch.setattr(cm, "require_chain_root", lambda: None)
    monkeypatch.setattr(cm, "check_add_node", lambda: None)
    monkeypatch.setattr(cm, "get_chain_status", _status)

    app = FastAPI()
    app.include_router(chain_router, prefix="/api/admin/chain")
    app.dependency_overrides[get_db] = _override_get_db

    db = TestingSessionLocal()
    try:
        db.add(User(username="c_admin", email="c_admin@e.com",
                    hashed_password=hash_password("x"), is_admin=True,
                    wallet_address="0x000000000000000000000000000000000000c001"))
        db.add(User(username="c_user", email="c_user@e.com",
                    hashed_password=hash_password("x"), is_admin=False,
                    wallet_address="0x000000000000000000000000000000000000c002"))
        db.commit()
    finally:
        db.close()

    with TestClient(app) as c:
        yield c

    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _drain_mutation_lock():
    """op 在后台线程里才释放 _mutation_lock（模块级单例）。用例结束后等它收尾，
    否则锁会泄漏到下一个用例，让本该 202 的请求变成 409。"""
    yield
    for _ in range(100):
        if cm._mutation_lock.acquire(blocking=False):
            cm._mutation_lock.release()
            return
        time.sleep(0.02)
    raise AssertionError("变更锁未被释放，后台 op 可能卡住了")


def _auth(username: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(data={'sub': username})}"}


BASE = "/api/admin/chain"

# (method, path, body) —— 全部端点
ENDPOINTS = [
    ("get", f"{BASE}/status", None),
    ("get", f"{BASE}/operations/abc123", None),
    ("post", f"{BASE}/start", None),
    ("post", f"{BASE}/stop", {"confirm": "STOP"}),
    ("post", f"{BASE}/nodes", {"make_signer": False}),
    ("delete", f"{BASE}/nodes/5", None),
    ("post", f"{BASE}/nodes/5/start", None),
    ("post", f"{BASE}/nodes/5/stop", None),
    ("post", f"{BASE}/signers", {"address": _SIGNERS[4], "authorize": False}),
    ("delete", f"{BASE}/signers/proposals/{_SIGNERS[4]}", None),
]


@pytest.mark.parametrize("method,path,body", ENDPOINTS)
def test_non_admin_forbidden(client, method, path, body):
    """普通登录用户碰不到任何一个私链管理端点。"""
    # 用 .request()：httpx 的 .get()/.delete() 不接受 json 关键字
    r = client.request(method.upper(), path, json=body, headers=_auth("c_user"))
    assert r.status_code == 403


@pytest.mark.parametrize("method,path,body", ENDPOINTS)
def test_anonymous_rejected(client, method, path, body):
    """无 token 一律拒绝（HTTPBearer 的 auto_error 返回 401/403）。"""
    r = client.request(method.upper(), path, json=body)
    assert r.status_code in (401, 403)


def test_admin_can_read_status(client):
    r = client.get(f"{BASE}/status", headers=_auth("c_admin"))
    assert r.status_code == 200
    assert r.json()["quorum"] == 3
    assert r.json()["online_signers"] == 5


def test_stop_rpc_entry_node_blocked(client):
    """node1 是后端结算的 RPC 入口：链没事，但后端会全挂 —— 单独停它必须被拒。"""
    r = client.post(f"{BASE}/nodes/1/stop", headers=_auth("c_admin"))
    assert r.status_code == 409
    assert r.json()["detail"]["error"]["code"] == "RPC_ENTRY_NODE"


def test_delete_rpc_entry_node_blocked(client):
    r = client.delete(f"{BASE}/nodes/1", headers=_auth("c_admin"))
    assert r.status_code == 409
    assert r.json()["detail"]["error"]["code"] == "RPC_ENTRY_NODE"


def test_stop_node_blocked_when_quorum_would_break(client, monkeypatch):
    """5 个签名者只剩 3 个在线（正好等于出块下限），再停一个就会让链停摆 → 硬拒。"""
    monkeypatch.setattr(cm, "get_chain_status", lambda: _status(running=(1, 2, 3)))
    r = client.post(f"{BASE}/nodes/3/stop", headers=_auth("c_admin"))
    assert r.status_code == 409
    err = r.json()["detail"]["error"]
    assert err["code"] == "QUORUM_WOULD_BREAK"
    assert err["details"] == {"online_signers_after": 2, "quorum_after": 3}


def test_stop_node_allowed_when_quorum_holds(client):
    """5 个全在线，停 1 个还剩 4 ≥ 3 —— 放行（do_stop_node 已被打成 no-op）。"""
    r = client.post(f"{BASE}/nodes/5/stop", headers=_auth("c_admin"))
    assert r.status_code == 202
    assert r.json()["op_id"]


def test_isolated_signer_does_not_count_toward_quorum(client, monkeypatch):
    """进程活着、RPC 也通，但 0 peer 的节点看不到别人的块，等于已掉出 quorum。
    全员孤立 → 链必然已停摆，没有出块可保护，护栏放行维护操作。"""
    monkeypatch.setattr(cm, "get_chain_status", lambda: _status(peers=0, producing=False))
    r = client.get(f"{BASE}/status", headers=_auth("c_admin"))
    assert r.json()["online_signers"] == 0
    assert all(n["isolated"] for n in r.json()["nodes"])
    r2 = client.post(f"{BASE}/nodes/5/stop", headers=_auth("c_admin"))
    assert r2.status_code == 202


def test_guardrail_not_disabled_by_transient_probe_miss(client, monkeypatch):
    """回归：护栏绝不能因为读数难看就自我关闭。

    链明明还在出块，但这一次探活把几个活着的签名者读成了掉线（逐节点 RPC 2s 超时，机器一忙
    就会发生），于是 online_signers(2) < quorum(3)。历史 bug：早期正是拿这个不等式当
    "链本就没在 quorum 上"的放行条件，一次 6.5s 的慢请求把 online 读少了，硬护栏直接被关掉，
    node3 被停、整条链当场停摆。只要链还在出块，护栏就必须照常拦。
    """
    monkeypatch.setattr(cm, "get_chain_status",
                        lambda: _status(running=(1, 2), producing=True))
    r = client.post(f"{BASE}/nodes/2/stop", headers=_auth("c_admin"))
    assert r.status_code == 409
    assert r.json()["detail"]["error"]["code"] == "QUORUM_WOULD_BREAK"


def test_guardrail_refuses_when_signer_set_unreadable(client, monkeypatch):
    """有活节点却读不到 clique 签名者集合 = 状态未知 → fail-closed，不许放行。"""
    monkeypatch.setattr(cm, "get_chain_status", lambda: _status(signers=()))
    r = client.post(f"{BASE}/nodes/5/stop", headers=_auth("c_admin"))
    assert r.status_code == 503
    assert r.json()["detail"]["error"]["code"] == "CHAIN_STATE_UNKNOWN"


def test_guardrail_allows_maintenance_when_chain_fully_down(client, monkeypatch):
    """链整个是停的：没有出块可保护，维护操作照常放行（否则停机后无法收拾残局）。"""
    monkeypatch.setattr(cm, "get_chain_status",
                        lambda: _status(running=(), signers=(), producing=False))
    r = client.post(f"{BASE}/nodes/5/stop", headers=_auth("c_admin"))
    assert r.status_code == 202


def test_remove_node_blocked_while_still_signer(client):
    """还在签名者集合里就删节点 → 拒绝，要求先投票罢免。"""
    r = client.delete(f"{BASE}/nodes/5", headers=_auth("c_admin"))
    assert r.status_code == 409
    assert r.json()["detail"]["error"]["code"] == "STILL_A_SIGNER"


def test_remove_non_signer_node_allowed(client, monkeypatch):
    monkeypatch.setattr(cm, "get_chain_status", lambda: _status(signers=(1, 2, 3, 4)))
    r = client.delete(f"{BASE}/nodes/5", headers=_auth("c_admin"))
    assert r.status_code == 202


def test_demote_signer_blocked_when_quorum_would_break(client, monkeypatch):
    """罢免签名者同样要过护栏。5 个签名者只剩 3 个在线（node1~3），罢免 node3：
    签名者变 4 个（下限仍是 3），但在线的只剩 node1、node2 两个 → 拒绝。"""
    monkeypatch.setattr(cm, "get_chain_status", lambda: _status(running=(1, 2, 3)))
    r = client.post(f"{BASE}/signers", json={"address": _SIGNERS[2], "authorize": False},
                    headers=_auth("c_admin"))
    assert r.status_code == 409
    assert r.json()["detail"]["error"]["code"] == "QUORUM_WOULD_BREAK"


def test_promote_existing_signer_rejected(client):
    r = client.post(f"{BASE}/signers", json={"address": _SIGNERS[1], "authorize": True},
                    headers=_auth("c_admin"))
    assert r.status_code == 409
    assert r.json()["detail"]["error"]["code"] == "ALREADY_SIGNER"


def test_stop_chain_requires_confirm(client):
    """停整条链是唯一能带走 RPC 入口节点的操作，必须显式 confirm=STOP。"""
    assert client.post(f"{BASE}/stop", json={}, headers=_auth("c_admin")).status_code == 422
    assert client.post(f"{BASE}/stop", json={"confirm": "yes"},
                       headers=_auth("c_admin")).status_code == 422
    assert client.post(f"{BASE}/stop", json={"confirm": "STOP"},
                       headers=_auth("c_admin")).status_code == 202


def test_node_not_found(client):
    r = client.post(f"{BASE}/nodes/99/stop", headers=_auth("c_admin"))
    assert r.status_code == 404
    assert r.json()["detail"]["error"]["code"] == "NODE_NOT_FOUND"


def test_only_one_mutation_at_a_time(client, monkeypatch):
    """单写锁：一个变更操作没跑完，第二个直接 409，避免"边启边停"打架。"""
    import threading
    release = threading.Event()
    monkeypatch.setattr(cm, "do_start_chain", lambda log: release.wait(timeout=10))
    try:
        first = client.post(f"{BASE}/start", headers=_auth("c_admin"))
        assert first.status_code == 202
        second = client.post(f"{BASE}/start", headers=_auth("c_admin"))
        assert second.status_code == 409
        assert second.json()["detail"]["error"]["code"] == "OPERATION_IN_PROGRESS"
    finally:
        release.set()


# --- 纯函数：不需要 client，也不碰任何进程 ---------------------------------

def test_v3_keystore_iv_is_always_16_bytes():
    """geth 要求 iv 恰好 16 字节，短一个字节就 panic 退出。

    eth_account 把随机 IV 转成 int 再转回 bytes，前导零字节会被吃掉，约 1/256 的新节点
    会拿到 15 字节 iv：geth `panic: cipher.NewCTR: IV length must equal block size`，
    RPC 刚起来就崩，而此时它可能已经被投成签名者 —— 出块下限白涨一格。
    """
    from eth_account import Account

    key = "0x" + "ab" * 32
    keystore = cm._v3_keystore(key, "pw")
    assert len(keystore["crypto"]["cipherparams"]["iv"]) == 32
    assert Account.decrypt(keystore, "pw").hex() == key[2:]  # 补零不影响解密


def test_v3_keystore_pads_iv_eaten_by_eth_account(monkeypatch):
    """喂一个"前导零被吃掉"的 iv（eth_account 的真实产物形态），必须被补回 16 字节。"""
    monkeypatch.setattr(cm.Account, "encrypt", lambda *a, **k: {
        "crypto": {"cipherparams": {"iv": "f57e6673d8056aa1a21a54edd0170b"}}})
    assert cm._v3_keystore("0x00", "pw")["crypto"]["cipherparams"]["iv"] == \
        "00f57e6673d8056aa1a21a54edd0170b"


def test_alloc_port_skips_ports_in_use():
    """端口必须真实探测：8554 就被一个无关进程占着（node4 的 authrpc 因此是 8564）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as squatter:
        squatter.bind(("127.0.0.1", 0))
        squatter.listen(1)
        busy = squatter.getsockname()[1]
        assert cm._port_in_use(busy) is True
        got = cm._alloc_port(busy, taken=set())
        assert got != busy
        assert got > busy


def test_alloc_port_skips_ports_already_in_registry():
    taken = {9100, 9101}
    got = cm._alloc_port(9100, taken)
    assert got not in (9100, 9101)
    assert got >= 9102


def test_build_geth_args_matches_start_all_ps1():
    """geth 参数向量必须与 start-all.ps1 一致：node1 带 --ws，node4 的 authrpc 是 8564。"""
    reg = _registry()
    node1 = dict(reg["nodes"][0], ws=8546, authrpc=8551, http=8545, p2p=30303)
    args = cm._build_geth_args(node1, 13370)
    assert "--ws" in args
    assert args[args.index("--ws.port") + 1] == "8546"
    assert args[args.index("--networkid") + 1] == "13370"
    assert args[args.index("--authrpc.port") + 1] == "8551"
    for flag in ("--mine", "--nodiscover", "--allow-insecure-unlock", "--ipcdisable", "--gcmode"):
        assert flag in args
    assert args[args.index("--miner.gasprice") + 1] == "0"
    assert args[args.index("--http.api") + 1] == "eth,net,web3,txpool,debug,clique,admin,miner"

    # node4：历史绕坑 authrpc=8564（8554 被占），且不该带 --ws
    node4 = {"index": 4, "datadir": "node4", "p2p": 30306, "http": 8549,
             "ws": None, "authrpc": 8564, "signer": _SIGNERS[3]}
    args4 = cm._build_geth_args(node4, 13370)
    assert args4[args4.index("--authrpc.port") + 1] == "8564"
    assert "--ws" not in args4


def test_quorum_derivation_not_configured():
    """出块下限是从签名者数推导的：5→3、6→4、7→4。
    所以 5 升到 6 不增加容错（N-quorum 都是 2），只是多要一台常开。"""
    assert _status(signers=(1, 2, 3, 4, 5))["quorum"] == 3
    assert _status(signers=(1, 2, 3, 4))["quorum"] == 3
    assert _status(signers=(1, 2, 3))["quorum"] == 2
