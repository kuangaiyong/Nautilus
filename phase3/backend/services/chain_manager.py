"""私有链（geth Clique POA）运维：启停链 / 启停节点 / 增删节点 / 签名者投票 / 状态。

管理员通过 /api/admin/chain 调用本模块。进程由 Python 直接起停（subprocess.Popen +
psutil），组网与签名者投票走 geth 的 HTTP RPC（clique/admin/miner 三组 API 本就在每个
节点的 --http.api 里，无需改动 geth 启动参数）。

六条踩过坑的硬约束，改动本文件前务必读完：

1. peering 是纯内存态，不持久化。所有节点跑 --nodiscover 且 datadir 下没有
   static-nodes.json，节点一重启就是 0 peer —— 进程活着、RPC 有响应、看着是绿的，
   但它已经悄悄掉出 quorum。故**任何 start/restart 之后都必须重跑 mesh_peers()**，
   且状态里必须暴露 isolated 标记。
2. enode 必须用 admin_nodeInfo().enode 原文，不能从 .id 拼。.id 是 node ID 不是 enode
   公钥，拼出来的 enode 会让 addPeer 返回 true 却永远连不上（start-all.ps1:57-59 已踩过）。
3. node1 是后端结算的唯一 RPC 入口（.env 的 PRIVATE_RPC）。单独停掉它时链还在出块
   （剩 4 个签名者 ≥ quorum 3），但后端所有链上写入立刻全挂 —— 故 node1 禁止单独停止/删除。
4. quorum 由 clique_getSigners() 推导（len//2+1），不做成配置项。能同时坏几个 = N - quorum：
   5 个签名者能坏 2 个（需 3 个在线），6 个也只能坏 2 个（却需 4 个在线）—— 5 升 6 不增加
   容错，只是多要一台常开；要真正提升容错得上 7 个（能坏 3 个）。
5. 护栏必须 fail-closed。_assert_quorum_preserved 的放行条件只能看"链是否还在出块"，
   绝不能看 online_signers < quorum —— 后者由逐节点 RPC 探活（2s 超时）聚合而来，机器
   一忙、任一次瞬时超时就会把读数压到 quorum 以下，护栏当场自我关闭（真实踩过）。
6. 新节点的 V3 keystore 必须走 _v3_keystore()，不能直接用 Account.encrypt() —— 后者会
   吃掉 IV 的前导零字节，约 1/256 概率写出 15 字节 iv，geth 起来 1 秒后 panic 退出，
   而那时它可能已经被投成签名者（出块下限白涨一格）。

节点注册表是 CHAIN_ROOT/nodes.json（只存拓扑，不存运行时状态）。start-all.ps1 也读它，
所以网页上加的节点重启机器后不会丢。PID 不持久化：psutil 按监听端口反查即可，后端重启
会自动认领已在跑的 geth，命令行手动起的节点也一样能管。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import urlparse

import psutil
import requests
from eth_account import Account

logger = logging.getLogger(__name__)

CHAIN_ROOT = os.getenv("CHAIN_ROOT", r"C:\nautilus-privatechain")
GETH_BIN = os.getenv("GETH_BIN", os.path.join(CHAIN_ROOT, "bin", "geth.exe"))
GENESIS_FILE = os.path.join(CHAIN_ROOT, "genesis.json")
PASSWORD_FILE = os.path.join(CHAIN_ROOT, "password.txt")
REGISTRY_FILE = os.path.join(CHAIN_ROOT, "nodes.json")

_GETH_PROC_NAME = "geth.exe"
_HTTP_API = "eth,net,web3,txpool,debug,clique,admin,miner"
_START_TIMEOUT = 30       # 秒，等新起的节点 RPC 就绪
_VOTE_TIMEOUT = 120       # 秒，等 clique 投票落地（提案要等提案方出块才生效）
_BLOCK_STALL_SECONDS = 15  # clique period=5s，超过 3 倍没出块即视为已停摆
_MAX_OPS = 20

# nodes.json 不存在时的初始拓扑，精确镜像 start-all.ps1 的 $nodes 表
# （含 node4 的 authrpc=8564 —— 8554 被一个无关进程占着，这是历史绕坑，不是笔误）。
_DEFAULT_NETWORK_ID = 13370
_DEFAULT_NODES = [
    {"index": 1, "datadir": "data", "p2p": 30303, "http": 8545, "ws": 8546, "authrpc": 8551,
     "signer": "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266"},
    {"index": 2, "datadir": "node2", "p2p": 30304, "http": 8547, "ws": None, "authrpc": 8552,
     "signer": "0x4d65c0e38d675346995f75ee36e0eb9f6464ff5d"},
    {"index": 3, "datadir": "node3", "p2p": 30305, "http": 8548, "ws": None, "authrpc": 8553,
     "signer": "0x7e95ded9117d8da617d9c40020456d5e823af30e"},
    {"index": 4, "datadir": "node4", "p2p": 30306, "http": 8549, "ws": None, "authrpc": 8564,
     "signer": "0x136c1cdba1a2f6da95adfa31844faf3324418b6b"},
    {"index": 5, "datadir": "node5", "p2p": 30307, "http": 8550, "ws": None, "authrpc": 8555,
     "signer": "0x7a9b0e426bcc0ff779cea28e647449b820e9c766"},
]

# Windows 下让 geth 脱离后端进程组独立存活：后端重启/退出不会带走整条链。
_DETACHED = 0
if os.name == "nt":
    _DETACHED = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP


class ChainError(Exception):
    """业务错误。api/chain_admin.py 把它映射成 HTTPException。"""

    def __init__(self, code: str, message: str, status: int = 400, details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


# ---------------------------------------------------------------------------
# 基础设施：日志 / RPC / 端口 / 进程
# ---------------------------------------------------------------------------

def _log(log: Optional[list], msg: str) -> None:
    logger.info("[chain] %s", msg)
    if log is not None:
        log.append(f"{datetime.now().strftime('%H:%M:%S')} {msg}")


# requests.Session 不是线程安全的，而 get_chain_status() 会并发探活所有节点 —— 每个线程
# 各持一个 Session：既保留连接复用，又不共享可变状态。
_local = threading.local()

# 常驻的探活线程池。必须与下面的 _executor（跑变更操作的那个）分开：变更操作线程内部会调
# get_chain_status()，若共用一个池就会自己等自己、把池饿死。
_probe_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="chainprobe")


def _rpc(port: int, method: str, params: Optional[list] = None, timeout: float = 3.0):
    """打某个节点的 JSON-RPC，失败抛异常。"""
    session = getattr(_local, "session", None)
    if session is None:
        session = _local.session = requests.Session()
    resp = session.post(
        f"http://127.0.0.1:{port}",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []},
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("error"):
        raise ChainError("RPC_ERROR", f"{method} 失败：{body['error'].get('message')}", status=502)
    return body.get("result")


def _try_rpc(port: int, method: str, params: Optional[list] = None, timeout: float = 3.0):
    """打 RPC，任何失败（含节点未启动）返回 None。用于探活与 best-effort 调用。"""
    try:
        return _rpc(port, method, params, timeout)
    except Exception:
        return None


def rpc_entry_port() -> int:
    """后端结算走的那个节点的 http 端口（.env 的 PRIVATE_RPC）。该节点禁止单独停止/删除。"""
    return urlparse(os.getenv("PRIVATE_RPC", "http://127.0.0.1:8545")).port or 8545


def _geth_pids_by_port(ports: set) -> dict:
    """监听端口 → geth 进程 PID。PID 不持久化，每次现查，故后端重启能自动认领在跑的节点。

    枚举不到连接表时**抛错而不是返回空字典** —— 返回空的话 _kill_node 会把「看不见 PID」
    误当成「进程没在跑」，静默跳过、还谎报停止成功，而 geth 其实还在跑。
    """
    found: dict = {}
    try:
        for conn in psutil.net_connections(kind="inet"):
            if (conn.status == psutil.CONN_LISTEN and conn.laddr and conn.pid
                    and conn.laddr.port in ports and conn.laddr.port not in found):
                found[conn.laddr.port] = conn.pid
    except (psutil.AccessDenied, PermissionError) as exc:
        raise ChainError(
            "PID_LOOKUP_DENIED",
            "枚举系统连接表被拒（psutil 权限不足），取不到 geth 的 PID，无法停止节点。"
            "请以管理员权限运行后端。",
            status=500,
        ) from exc
    out = {}
    for port, pid in found.items():
        try:
            if psutil.Process(pid).name().lower() == _GETH_PROC_NAME:
                out[port] = pid
        except psutil.Error:
            continue
    return out


def _port_in_use(port: int) -> bool:
    # 不设 SO_REUSEADDR：Windows 上它会让 bind 到别人占着的端口也成功，探测就失真了。
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


def _alloc_port(start: int, taken: set) -> int:
    """从 start 起找第一个既不在注册表、又没被任何进程占用的端口。
    必须真实探测而非简单递增：8554 就被一个无关进程占着（node4 的 authrpc 因此是 8564）。"""
    for port in range(start, start + 200):
        if port in taken or _port_in_use(port):
            continue
        taken.add(port)
        return port
    raise ChainError("NO_FREE_PORT", f"从 {start} 起 200 个端口内找不到空闲端口", status=503)


def _read_password() -> str:
    # 与 geth --password 的行为一致：取第一行、只去掉行尾换行。
    with open(PASSWORD_FILE, "r", encoding="utf-8") as fh:
        return fh.readline().rstrip("\r\n")


def _v3_keystore(private_key, password: str) -> dict:
    """生成 geth 能读的 V3 keystore。

    eth_account 把 16 字节随机 IV 先转成 int 再转回 bytes，**前导零字节会被吃掉**，
    于是约 1/256 的概率写出 15 字节的 iv。eth_account 自己解得开（它按 int 读），
    但 geth 是 `panic: cipher.NewCTR: IV length must equal block size` —— 新节点
    RPC 刚起来就崩，进程死了却已经被投成签名者，出块下限白涨一格。
    左边补零即可还原出加密时用的那 16 字节，不影响解密。
    """
    keystore = Account.encrypt(private_key, password)
    iv = keystore["crypto"]["cipherparams"]["iv"]
    keystore["crypto"]["cipherparams"]["iv"] = iv.rjust(32, "0")
    return keystore


def require_chain_root() -> None:
    """CHAIN_ROOT / GETH_BIN 不在磁盘上就别装懂 —— 直接 503（部署到别的机器时的天然保护）。"""
    if not os.path.isdir(CHAIN_ROOT):
        raise ChainError("CHAIN_ROOT_MISSING", f"私链目录不存在：{CHAIN_ROOT}", status=503)
    if not os.path.isfile(GETH_BIN):
        raise ChainError("GETH_BIN_MISSING", f"geth 可执行文件不存在：{GETH_BIN}", status=503)


# ---------------------------------------------------------------------------
# 注册表 nodes.json
# ---------------------------------------------------------------------------

def load_registry() -> dict:
    require_chain_root()
    if not os.path.isfile(REGISTRY_FILE):
        reg = {"network_id": _DEFAULT_NETWORK_ID, "nodes": [dict(n) for n in _DEFAULT_NODES]}
        save_registry(reg)
        logger.info("[chain] 首次使用，已按 start-all.ps1 的拓扑生成 %s", REGISTRY_FILE)
        return reg
    with open(REGISTRY_FILE, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_registry(reg: dict) -> None:
    tmp = REGISTRY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(reg, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, REGISTRY_FILE)  # 原子替换，避免半截文件把 start-all.ps1 也带崩


def _registry_ports(reg: dict) -> set:
    ports = set()
    for node in reg["nodes"]:
        for key in ("p2p", "http", "ws", "authrpc"):
            if node.get(key):
                ports.add(node[key])
    return ports


def _find_node(reg: dict, index: int) -> dict:
    node = next((n for n in reg["nodes"] if n["index"] == index), None)
    if node is None:
        raise ChainError("NODE_NOT_FOUND", f"节点 {index} 不存在", status=404)
    return node


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------

def _build_geth_args(node: dict, network_id: int) -> list:
    """geth 参数向量的唯一生成处，与 start-all.ps1 的 $a 数组逐项一致。"""
    args = [
        GETH_BIN,
        "--datadir", os.path.join(CHAIN_ROOT, node["datadir"]),
        "--networkid", str(network_id),
        "--port", str(node["p2p"]),
        "--http", "--http.addr", "127.0.0.1", "--http.port", str(node["http"]),
        "--http.api", _HTTP_API, "--http.corsdomain", "*", "--http.vhosts", "*",
        "--mine", "--miner.etherbase", node["signer"],
        "--unlock", node["signer"], "--password", PASSWORD_FILE,
        "--allow-insecure-unlock", "--miner.gasprice", "0",
        "--gcmode", "archive", "--nodiscover", "--maxpeers", "25",
        "--authrpc.port", str(node["authrpc"]), "--ipcdisable",
    ]
    if node.get("ws"):
        args += ["--ws", "--ws.addr", "127.0.0.1", "--ws.port", str(node["ws"])]
    return args


def _probe_node(node: dict, pid_map: dict) -> dict:
    port = node["http"]
    state = {
        "index": node["index"], "datadir": node["datadir"],
        "p2p": node["p2p"], "http": port, "ws": node.get("ws"), "authrpc": node["authrpc"],
        "signer": node["signer"].lower(),
        "pid": pid_map.get(port),
        "running": False, "block_number": None, "peer_count": None,
        "mining": None, "enode": None, "isolated": False, "is_signer": False,
        "is_rpc_entry": port == rpc_entry_port(),
    }
    block_hex = _try_rpc(port, "eth_blockNumber")
    if block_hex is None:
        return state  # RPC 不通即视为未运行（残留进程没有意义）
    state["running"] = True
    state["block_number"] = int(block_hex, 16)
    peers = _try_rpc(port, "net_peerCount")
    state["peer_count"] = int(peers, 16) if peers is not None else 0
    state["mining"] = bool(_try_rpc(port, "eth_mining"))
    state["enode"] = (_try_rpc(port, "admin_nodeInfo") or {}).get("enode")
    return state


def _signer_online(state: dict, signer_count: int) -> bool:
    """签名者是否真的在为 quorum 出力。
    进程活着还不够 —— 0 peer 的节点看不到别人的块、也无法轮流出块，等于已掉出 quorum。
    （只有 1 个签名者时它独自封块，无需互联。）"""
    if not state["running"]:
        return False
    if signer_count < 2:
        return True
    return (state["peer_count"] or 0) > 0


def _probe_all(nodes: list) -> list:
    """并发探测所有节点。组网只要这一层（running + enode），不必再拉 clique 与块信息。"""
    try:
        pid_map = _geth_pids_by_port({n["http"] for n in nodes})
    except ChainError:
        # 只读路径降级：判活本来就靠 RPC 而不是 PID，看不到 PID 只影响展示。
        # 停止类操作会让这个错误抛出去（见 _kill_node），不会假装成功。
        pid_map = {}
    return list(_probe_pool.map(lambda n: _probe_node(n, pid_map), nodes))


def get_chain_status() -> dict:
    reg = load_registry()
    nodes = reg["nodes"]
    states = _probe_all(nodes)

    for state in states:
        # 进程活着、RPC 有响应、却 0 peer —— UI 必须把这种"绿着骗人"的节点标出来
        state["isolated"] = bool(state["running"] and state["peer_count"] == 0 and len(nodes) > 1)

    live = [s for s in states if s["running"]]
    signers: list = []
    proposals: dict = {}
    latest_ts = None
    if live:
        port = live[0]["http"]
        signers = [a.lower() for a in (_try_rpc(port, "clique_getSigners") or [])]
        proposals = _try_rpc(port, "clique_proposals") or {}
        block = _try_rpc(port, "eth_getBlockByNumber", ["latest", False])
        if block and block.get("timestamp") is not None:
            latest_ts = int(block["timestamp"], 16)

    for state in states:
        state["is_signer"] = state["signer"] in signers

    # 必须判 is not None：创世块时间戳是 0x0，用真假判断会把「停在 block 0 的新链」
    # 当成没数据，UI 上显示成「已停摆」。
    seconds_since_block = int(time.time()) - latest_ts if latest_ts is not None else None
    return {
        "running": bool(live),
        # 单次 RPC 判活：拿最新块的时间戳算"多久没出块"，不必像脚本那样采样两次干等 8 秒
        "producing": seconds_since_block is not None and seconds_since_block < _BLOCK_STALL_SECONDS,
        "latest_block": max((s["block_number"] or 0) for s in states) if live else None,
        "seconds_since_block": seconds_since_block,
        "signers": signers,
        "quorum": (len(signers) // 2 + 1) if signers else 0,
        "online_signers": sum(1 for s in states if s["is_signer"] and _signer_online(s, len(signers))),
        "proposals": proposals,
        "rpc_entry_port": rpc_entry_port(),
        "nodes": states,
    }


# ---------------------------------------------------------------------------
# 护栏
# ---------------------------------------------------------------------------

def _assert_not_rpc_entry(node: dict) -> None:
    if node["http"] == rpc_entry_port():
        raise ChainError(
            "RPC_ENTRY_NODE",
            f"node{node['index']} 是后端结算的 RPC 入口（PRIVATE_RPC），单独停掉它会让所有华币"
            f"结算和任务完成接口立刻失败。要下线它请用「停止整条链」。",
            status=409,
        )


def _assert_quorum_preserved(status: dict, stopping: set = frozenset(),
                             removing_signers: set = frozenset()) -> None:
    """操作后仍在线的签名者不足 quorum 就直接拒绝。跌破 quorum = 整条链停止出块 =
    后端所有链上写入全挂，所以这里是硬拒绝而不是警告。

    三条放行/拒绝的前置判断都必须 fail-closed —— 拿不准就拒，不能因为读数不好看就把护栏关掉。
    """
    # 一个活节点都没有：链本来就是停的，没有出块可保护，放行维护操作。
    if not status["running"]:
        return

    # 有活节点却读不到 clique 签名者集合 = 状态未知，根本无法判断这一刀会不会砍死链。
    if not status["signers"]:
        raise ChainError(
            "CHAIN_STATE_UNKNOWN",
            "读不到 clique 签名者集合，无法判断该操作是否会让链停止出块，已拒绝。请重试。",
            status=503,
        )

    # 链已经停摆（历史故障 / 早就跌破下限），同样没有出块可保护，放行维护。
    # 这里必须用"是否还在出块"判断，绝不能用 online_signers < quorum：后者是逐节点 RPC
    # 探活（2s 超时）聚合出来的，机器一忙、任何一次瞬时超时都会把它压到 quorum 以下，
    # 于是整个硬护栏被悄悄关掉 —— 而那恰恰是最需要护栏的时刻。（真实踩过：E2E 里
    # 一次 6.5s 的慢请求让 online 读成 3 < 4，护栏放行，node3 被停，链当场停摆。）
    if not status["producing"]:
        return

    signers_after = [s for s in status["signers"] if s not in removing_signers]
    quorum_after = (len(signers_after) // 2 + 1) if signers_after else 0
    online_after = sum(
        1 for s in status["nodes"]
        if s["signer"] in signers_after
        and s["index"] not in stopping
        and _signer_online(s, len(signers_after))
    )
    if online_after < quorum_after:
        raise ChainError(
            "QUORUM_WOULD_BREAK",
            f"该操作会让在线签名者降到 {online_after} 个，低于出块下限 {quorum_after} 个，"
            f"整条链会停止出块、后端所有华币结算随之失败。已拒绝。",
            status=409,
            details={"online_signers_after": online_after, "quorum_after": quorum_after},
        )


# ---------------------------------------------------------------------------
# 进程与组网
# ---------------------------------------------------------------------------

def _wait_rpc(port: int, timeout: int = _START_TIMEOUT) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _try_rpc(port, "eth_blockNumber", timeout=1.0) is not None:
            return True
        time.sleep(1.0)
    return False


def _spawn_node(node: dict, network_id: int, log: Optional[list] = None) -> None:
    port = node["http"]
    if _try_rpc(port, "eth_blockNumber") is not None:
        _log(log, f"node{node['index']} 已在运行，跳过启动")
        return

    log_path = os.path.join(CHAIN_ROOT, f"node{node['index']}-restart.log")
    handle = open(log_path, "wb")  # geth 全部日志走 stderr
    try:
        subprocess.Popen(
            _build_geth_args(node, network_id),
            stdout=subprocess.DEVNULL, stderr=handle,
            creationflags=_DETACHED, close_fds=True,
        )
    finally:
        handle.close()  # 子进程已拿到自己的句柄副本

    if _wait_rpc(port):
        _log(log, f"node{node['index']} 已启动（p2p={node['p2p']} http={port}）")
    else:
        raise ChainError("NODE_START_TIMEOUT",
                         f"node{node['index']} 启动后 {_START_TIMEOUT}s 内 RPC 未就绪，"
                         f"详见 node{node['index']}-restart.log", status=500)


def _kill_node(node: dict, log: Optional[list] = None) -> None:
    port = node["http"]
    pid = _geth_pids_by_port({port}).get(port)
    if pid is None:
        _log(log, f"node{node['index']} 未在运行")
        return
    try:
        proc = psutil.Process(pid)
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except psutil.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    except psutil.NoSuchProcess:
        pass
    _log(log, f"node{node['index']} 已停止（pid={pid}）")


def mesh_peers(states: list, log: Optional[list] = None) -> int:
    """全网 addPeer。start/restart 之后必须调用 —— 否则节点 RPC 通但 0 peer，
    看着是绿的、实际已掉出 quorum。"""
    live = [s for s in states if s["running"] and s["enode"]]
    pairs = 0
    for src in live:
        for dst in live:
            if src["index"] == dst["index"]:
                continue
            # 必须用 admin_nodeInfo().enode 原文：.id 不是 enode 公钥，拼出来的 enode
            # 会让 addPeer 返回 true 却永远连不上（start-all.ps1:57-59 的教训）。
            if _try_rpc(src["http"], "admin_addPeer", [dst["enode"]]) is not None:
                pairs += 1
    _log(log, f"全网 mesh 完成（{len(live)} 个在线节点，{pairs} 条 addPeer）")
    return pairs


# ---------------------------------------------------------------------------
# 变更操作（check_* 同步跑护栏，do_* 在后台线程里干活）
# ---------------------------------------------------------------------------

def do_start_chain(log: Optional[list] = None) -> None:
    reg = load_registry()
    for node in reg["nodes"]:
        _spawn_node(node, reg["network_id"], log)
    mesh_peers(_probe_all(reg["nodes"]), log)
    # 必须组网后再取状态：mesh 之前每个节点都是 0 peer，online_signers 会是 0
    status = get_chain_status()
    _log(log, f"在线签名者 {status['online_signers']}/{len(status['signers'])}"
              f"（出块下限 {status['quorum']}），最新块高 {status['latest_block']}")


def do_stop_chain(log: Optional[list] = None) -> None:
    for node in load_registry()["nodes"]:
        _kill_node(node, log)
    _log(log, "私链已全部停止")


def check_start_node(index: int) -> None:
    _find_node(load_registry(), index)


def do_start_node(index: int, log: Optional[list] = None) -> None:
    reg = load_registry()
    _spawn_node(_find_node(reg, index), reg["network_id"], log)
    # 重启的节点是 0 peer 的孤岛，必须重新组网才算真的回到 quorum
    mesh_peers(_probe_all(reg["nodes"]), log)


def check_stop_node(index: int) -> None:
    node = _find_node(load_registry(), index)
    _assert_not_rpc_entry(node)
    _assert_quorum_preserved(get_chain_status(), stopping={index})


def do_stop_node(index: int, log: Optional[list] = None) -> None:
    _kill_node(_find_node(load_registry(), index), log)


def check_add_node() -> None:
    require_chain_root()
    if not os.path.isfile(GENESIS_FILE):
        raise ChainError("GENESIS_MISSING", f"genesis.json 不存在：{GENESIS_FILE}", status=503)
    if not os.path.isfile(PASSWORD_FILE):
        raise ChainError("PASSWORD_MISSING", f"password.txt 不存在：{PASSWORD_FILE}", status=503)


def _alloc_datadir(index: int) -> str:
    """给新节点挑一个磁盘上还不存在的目录名。

    不能直接用 node{index}：删节点时 datadir 是**保留**的，而 index 又是 max+1，所以删掉
    node6 再加一个节点会重新算出 6、撞上被删节点的遗留目录 —— 新节点会带着别人的 chaindata
    和 keystore 启动，"保留 datadir 以便手工恢复"也就没意义了。
    """
    name = f"node{index}"
    seq = 2
    while os.path.exists(os.path.join(CHAIN_ROOT, name)):
        name = f"node{index}_{seq}"
        seq += 1
    return name


def do_add_node(make_signer: bool, log: Optional[list] = None) -> dict:
    reg = load_registry()
    index = max((n["index"] for n in reg["nodes"]), default=0) + 1
    taken = _registry_ports(reg)
    node = {
        "index": index,
        "datadir": _alloc_datadir(index),
        "p2p": _alloc_port(30303, taken),
        "http": _alloc_port(8545, taken),
        "ws": None,
        "authrpc": _alloc_port(8551, taken),
        "signer": "",
    }

    datadir = os.path.join(CHAIN_ROOT, node["datadir"])
    try:
        os.makedirs(os.path.join(datadir, "keystore"))  # _alloc_datadir 已保证它不存在

        # 用 eth_account 直接生成 V3 keystore，省掉 `geth account new` 子进程和它的 stdout 解析
        account = Account.create()
        keystore = _v3_keystore(account.key, _read_password())
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S.%f000Z")
        ks_path = os.path.join(datadir, "keystore", f"UTC--{stamp}--{account.address[2:].lower()}")
        with open(ks_path, "w", encoding="utf-8") as fh:
            json.dump(keystore, fh)
        node["signer"] = account.address.lower()

        # 必须用同一份 genesis.json：换一份会导致 genesis hash 不同、节点自成一条链、被其它节点拒绝
        result = subprocess.run([GETH_BIN, "--datadir", datadir, "init", GENESIS_FILE],
                                capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise ChainError("GETH_INIT_FAILED", "geth init 失败", status=500,
                             details={"stderr": (result.stderr or "")[-400:]})
    except BaseException:
        # 半成品目录里躺着刚生成的私钥，且下次重试会再往里塞一个 keystore —— 直接清掉
        shutil.rmtree(datadir, ignore_errors=True)
        raise

    _log(log, f"node{index} 签名者地址 {node['signer']}")
    _log(log, f"node{index} genesis 初始化完成（datadir {node['datadir']}\\）")

    reg["nodes"].append(node)
    save_registry(reg)
    _log(log, f"node{index} 已写入 nodes.json"
              f"（p2p={node['p2p']} http={node['http']} authrpc={node['authrpc']}）")

    _spawn_node(node, reg["network_id"], log)
    mesh_peers(_probe_all(reg["nodes"]), log)

    if make_signer:
        do_propose_signer(node["signer"], True, log)  # 内部已等到投票生效并记日志
    return node


def check_remove_node(index: int) -> None:
    node = _find_node(load_registry(), index)
    _assert_not_rpc_entry(node)
    status = get_chain_status()
    state = next(s for s in status["nodes"] if s["index"] == index)
    if state["is_signer"]:
        raise ChainError("STILL_A_SIGNER",
                         f"node{index} 还在签名者集合里，请先投票罢免它再删除", status=409)
    _assert_quorum_preserved(status, stopping={index})


def do_remove_node(index: int, log: Optional[list] = None) -> None:
    reg = load_registry()
    node = _find_node(reg, index)
    states = _probe_all(reg["nodes"])
    state = next(s for s in states if s["index"] == index)

    # 先趁节点还活着抓 enode，停了就拿不到了
    if state["enode"]:
        for other in states:
            if other["index"] != index and other["running"]:
                _try_rpc(other["http"], "admin_removePeer", [state["enode"]])
        _log(log, "已从其余节点摘除该 peer")

    _kill_node(node, log)
    reg["nodes"] = [n for n in reg["nodes"] if n["index"] != index]
    save_registry(reg)
    _log(log, f"node{index} 已移出 nodes.json；datadir {node['datadir']}\\ 保留在磁盘上（可加回恢复）")


def _read_signers() -> Optional[list]:
    """从任意一个在线节点读签名者集合；全都不在线时返回 None。"""
    for node in load_registry()["nodes"]:
        result = _try_rpc(node["http"], "clique_getSigners")
        if result is not None:
            return [a.lower() for a in result]
    return None


def _wait_signer(address: str, present: bool, timeout: int = _VOTE_TIMEOUT) -> bool:
    """等 clique 投票落地。只读 clique_getSigners —— 别在这个 3s 轮询里调 get_chain_status()，
    那会连带做一次全机 psutil 套接字扫描 + 每个节点 4 次 RPC，一次投票就是几百次白跑的调用。"""
    addr = address.lower()
    deadline = time.time() + timeout
    while time.time() < deadline:
        signers = _read_signers()
        if signers is not None and (addr in signers) == present:
            return True
        time.sleep(3)
    return False


def check_propose_signer(address: str, authorize: bool) -> None:
    status = get_chain_status()
    addr = address.lower()
    if authorize:
        if addr in status["signers"]:
            raise ChainError("ALREADY_SIGNER", f"{addr} 已经是签名者", status=409)
    else:
        if addr not in status["signers"]:
            raise ChainError("NOT_A_SIGNER", f"{addr} 不是签名者", status=409)
        _assert_quorum_preserved(status, removing_signers={addr})
    if not any(s["is_signer"] and s["running"] for s in status["nodes"]):
        raise ChainError("NO_LIVE_SIGNER", "没有在线的签名者节点可以投票", status=503)


def do_propose_signer(address: str, authorize: bool, log: Optional[list] = None) -> None:
    status = get_chain_status()
    voters = [s for s in status["nodes"] if s["is_signer"] and s["running"]]
    for voter in voters:
        _try_rpc(voter["http"], "clique_propose", [address.lower(), authorize])
    action = "选入" if authorize else "罢免"
    # clique 投票是最终一致的：提案要等提案方轮到自己出块才落地，不是调用即生效
    _log(log, f"已在 {len(voters)} 个在线签名者上投票{action} {address.lower()}，等待出块生效…")
    if _wait_signer(address, present=authorize):
        _log(log, f"{action}已生效，当前签名者 {len(_read_signers() or [])} 个")
    else:
        _log(log, f"[警告] {_VOTE_TIMEOUT}s 内投票未生效，可稍后重新发起")


def do_discard_proposal(address: str, log: Optional[list] = None) -> None:
    status = get_chain_status()
    for voter in [s for s in status["nodes"] if s["is_signer"] and s["running"]]:
        _try_rpc(voter["http"], "clique_discard", [address.lower()])
    _log(log, f"已撤销对 {address.lower()} 的投票提案")


# ---------------------------------------------------------------------------
# 异步操作追踪：起链要 30~45 秒，不能阻塞事件循环（同 services/audit_trail.py 的做法）
# ---------------------------------------------------------------------------

_ops: dict = {}
_ops_lock = threading.Lock()
_mutation_lock = threading.Lock()  # 同一时刻只允许一个变更类操作，避免"边启边停"打架
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="chainops")


def submit_op(action: str, work: Callable, precheck: Optional[Callable] = None) -> str:
    """跑一个变更操作。precheck 在持锁状态下同步执行，护栏不通过就直接抛（API 返回 409，
    不会产生一个注定失败的 op）；work 在后台线程里跑，前端轮询 get_op()。"""
    if not _mutation_lock.acquire(blocking=False):
        raise ChainError("OPERATION_IN_PROGRESS", "已有另一个私链操作正在执行，请稍候", status=409)
    try:
        if precheck is not None:
            precheck()
    except BaseException:
        _mutation_lock.release()
        raise

    op = {
        "op_id": uuid.uuid4().hex[:12], "action": action, "state": "running",
        "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None,
        "log": [], "error": None,
    }
    with _ops_lock:
        _ops[op["op_id"]] = op
        for stale in sorted(_ops, key=lambda k: _ops[k]["started_at"])[:-_MAX_OPS]:
            del _ops[stale]

    def _run():
        try:
            work(op["log"])
            op["state"] = "done"
        except ChainError as exc:
            op["state"] = "failed"
            op["error"] = {"code": exc.code, "message": exc.message}
            op["log"].append(f"[失败] {exc.message}")
        except Exception as exc:  # noqa: BLE001 - 后台线程必须兜住一切，否则线程静默死掉
            logger.exception("[chain] 操作 %s 失败", action)
            op["state"] = "failed"
            op["error"] = {"code": "INTERNAL", "message": str(exc)}
            op["log"].append(f"[失败] {exc}")
        finally:
            op["finished_at"] = datetime.now(timezone.utc).isoformat()
            _mutation_lock.release()

    _executor.submit(_run)
    return op["op_id"]


def get_op(op_id: str) -> dict:
    with _ops_lock:
        op = _ops.get(op_id)
    if op is None:
        raise ChainError("OP_NOT_FOUND", f"操作 {op_id} 不存在或已过期", status=404)
    return op
