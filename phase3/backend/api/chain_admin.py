"""私有链运维 API（仅管理员）。

挂在 /api/admin/chain，每个端点都过 get_current_admin_user —— 非管理员一律 403。
实际干活的是 services/chain_manager.py（那里有四条必读的硬约束）。

变更类端点返回 202 + op_id：起链要 30~45 秒，不能把 HTTP 请求挂在那儿。护栏在
submit_op 里持锁同步跑完，所以护栏不通过是当场 409，不会先给一个注定失败的 op_id。
端点写成 sync def，FastAPI 会丢进线程池，阻塞的 RPC/子进程不会卡住事件循环。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field

_ADDRESS_RE = "^0x[0-9a-fA-F]{40}$"

from models.database import User
from services import chain_manager
from services.chain_manager import ChainError
from utils.auth import get_current_admin_user

logger = logging.getLogger(__name__)

router = APIRouter()


class AddNodeRequest(BaseModel):
    """新节点默认只同步不出块（对共识零影响）。要它出块得走 clique 投票。"""
    make_signer: bool = False


class StopChainRequest(BaseModel):
    # 停整条链会让后端所有华币结算和任务完成接口失败，故要求显式确认
    confirm: str = Field(..., pattern="^STOP$")


class SignerVoteRequest(BaseModel):
    address: str = Field(..., pattern=_ADDRESS_RE)
    authorize: bool


class OperationAccepted(BaseModel):
    op_id: str


def _http(exc: ChainError) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
    )


@router.get("/status")
def get_status(current_admin: User = Depends(get_current_admin_user)):
    """链 + 各节点的实时状态。只读，不加变更锁；前端按秒级轮询它。"""
    try:
        return chain_manager.get_chain_status()
    except ChainError as exc:
        raise _http(exc)


@router.get("/operations/{op_id}")
def get_operation(op_id: str, current_admin: User = Depends(get_current_admin_user)):
    """轮询一个变更操作的进度与日志。"""
    try:
        return chain_manager.get_op(op_id)
    except ChainError as exc:
        raise _http(exc)


@router.post("/start", response_model=OperationAccepted, status_code=status.HTTP_202_ACCEPTED)
def start_chain(current_admin: User = Depends(get_current_admin_user)):
    """启动注册表里的所有节点并全网组网。幂等：已在跑的节点会跳过，但仍会重新组网。"""
    logger.info("admin %s 启动私链", current_admin.username)
    try:
        return OperationAccepted(op_id=chain_manager.submit_op(
            "start_chain", chain_manager.do_start_chain,
            precheck=chain_manager.require_chain_root))
    except ChainError as exc:
        raise _http(exc)


@router.post("/stop", response_model=OperationAccepted, status_code=status.HTTP_202_ACCEPTED)
def stop_chain(body: StopChainRequest, current_admin: User = Depends(get_current_admin_user)):
    """停止所有节点。这是唯一能带走 RPC 入口节点的操作，故需 confirm=STOP。"""
    logger.warning("admin %s 停止整条私链", current_admin.username)
    try:
        return OperationAccepted(op_id=chain_manager.submit_op(
            "stop_chain", chain_manager.do_stop_chain,
            precheck=chain_manager.require_chain_root))
    except ChainError as exc:
        raise _http(exc)


@router.post("/nodes", response_model=OperationAccepted, status_code=status.HTTP_202_ACCEPTED)
def add_node(body: AddNodeRequest, current_admin: User = Depends(get_current_admin_user)):
    """新增节点：探测分配端口 → 生成签名者密钥 → geth init → 启动 → 全网组网。"""
    logger.info("admin %s 新增私链节点 (make_signer=%s)", current_admin.username, body.make_signer)
    try:
        return OperationAccepted(op_id=chain_manager.submit_op(
            "add_node",
            lambda log: chain_manager.do_add_node(body.make_signer, log),
            precheck=chain_manager.check_add_node,
        ))
    except ChainError as exc:
        raise _http(exc)


@router.delete("/nodes/{index}", response_model=OperationAccepted,
               status_code=status.HTTP_202_ACCEPTED)
def remove_node(index: int, current_admin: User = Depends(get_current_admin_user)):
    """摘除节点：停进程 + 从其余节点 removePeer + 移出注册表。datadir 保留在磁盘上。"""
    logger.warning("admin %s 删除私链节点 %s", current_admin.username, index)
    try:
        return OperationAccepted(op_id=chain_manager.submit_op(
            "remove_node",
            lambda log: chain_manager.do_remove_node(index, log),
            precheck=lambda: chain_manager.check_remove_node(index),
        ))
    except ChainError as exc:
        raise _http(exc)


@router.post("/nodes/{index}/start", response_model=OperationAccepted,
             status_code=status.HTTP_202_ACCEPTED)
def start_node(index: int, current_admin: User = Depends(get_current_admin_user)):
    """启动单个节点，并强制重新全网组网（否则它是 0 peer 的孤岛，等于没回到 quorum）。"""
    logger.info("admin %s 启动私链节点 %s", current_admin.username, index)
    try:
        return OperationAccepted(op_id=chain_manager.submit_op(
            "start_node",
            lambda log: chain_manager.do_start_node(index, log),
            precheck=lambda: chain_manager.check_start_node(index),
        ))
    except ChainError as exc:
        raise _http(exc)


@router.post("/nodes/{index}/stop", response_model=OperationAccepted,
             status_code=status.HTTP_202_ACCEPTED)
def stop_node(index: int, current_admin: User = Depends(get_current_admin_user)):
    """停止单个节点。跌破出块下限、或该节点是后端 RPC 入口 → 409。"""
    logger.warning("admin %s 停止私链节点 %s", current_admin.username, index)
    try:
        return OperationAccepted(op_id=chain_manager.submit_op(
            "stop_node",
            lambda log: chain_manager.do_stop_node(index, log),
            precheck=lambda: chain_manager.check_stop_node(index),
        ))
    except ChainError as exc:
        raise _http(exc)


@router.post("/signers", response_model=OperationAccepted, status_code=status.HTTP_202_ACCEPTED)
def vote_signer(body: SignerVoteRequest, current_admin: User = Depends(get_current_admin_user)):
    """clique 投票选入/罢免签名者。最终一致 —— 提案要等提案方出块才落地，op 会等到生效为止。"""
    logger.warning("admin %s 投票 %s 签名者 %s", current_admin.username,
                   "选入" if body.authorize else "罢免", body.address)
    try:
        return OperationAccepted(op_id=chain_manager.submit_op(
            "vote_signer",
            lambda log: chain_manager.do_propose_signer(body.address, body.authorize, log),
            precheck=lambda: chain_manager.check_propose_signer(body.address, body.authorize),
        ))
    except ChainError as exc:
        raise _http(exc)


@router.delete("/signers/proposals/{address}", response_model=OperationAccepted,
               status_code=status.HTTP_202_ACCEPTED)
def discard_proposal(address: str = Path(..., pattern=_ADDRESS_RE),
                     current_admin: User = Depends(get_current_admin_user)):
    """撤销一个尚未生效的投票提案。"""
    logger.info("admin %s 撤销签名者提案 %s", current_admin.username, address)
    try:
        return OperationAccepted(op_id=chain_manager.submit_op(
            "discard_proposal",
            lambda log: chain_manager.do_discard_proposal(address, log),
            precheck=chain_manager.require_chain_root))
    except ChainError as exc:
        raise _http(exc)


# main.py 把这两个只读端点从全局 default_limits（200/hour）里豁免出去。
# 管理台要秒级轮询链状态与操作进度，200/hour 撑不住十分钟；而 slowapi 的 middleware 对
# 所有路由无条件套用 default_limits（路由级 @limiter.limit 也覆盖不掉，见 extension.py:614-633），
# 唯一出口就是 _exempt_routes。变更类端点不豁免，仍受全局限流 + 单写锁双重约束。
RATE_LIMIT_EXEMPT = [get_status, get_operation]
