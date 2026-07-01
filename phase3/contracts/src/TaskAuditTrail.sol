// SPDX-License-Identifier: MIT
pragma solidity ^0.8.21;

/**
 * @title TaskAuditTrail
 * @notice 任务生命周期可信追踪：发布/抢单/派单/提交/评审/完成等动作的哈希存证 + 主体背书。
 *
 * 方案 A-ii：由动作主体（智能体 / 发布者的托管钱包）亲自发起 record 交易，`msg.sender`
 * 即行为主体，交易签名本身就是不可抵赖的背书。合约仅锚定「内容哈希 + 主体 + 时间」，
 * 原文与业务数据留在链下（MySQL）；校验时用链下源数据重算哈希并与本合约事件比对，
 * 从而证明链下记录自锚定后未被篡改。
 *
 * 动作类型 action（与后端 services/audit_trail.py 的 Action 对齐）：
 *   1=PUBLISH 2=BID 3=AWARD 4=ACCEPT 5=SUBMIT 6=REVIEW 7=COMPLETE
 */
contract TaskAuditTrail {
    event AuditRecord(
        uint256 indexed taskId,
        uint8 indexed action,
        address indexed actor,
        bytes32 contentHash,
        uint256 timestamp,
        uint256 seq
    );

    /// @notice 已锚定的记录总数（同时用作自增全局序号）
    uint256 public totalRecords;
    /// @notice 每个任务已锚定的记录数
    mapping(uint256 => uint256) public taskRecordCount;

    /**
     * @notice 锚定一条任务生命周期记录。
     * @param taskId 链下任务主键 id
     * @param action 动作类型（见合约头注释）
     * @param contentHash 规范化原文的 keccak256
     * @return seq 本条记录的全局序号
     */
    function record(uint256 taskId, uint8 action, bytes32 contentHash)
        external
        returns (uint256 seq)
    {
        seq = totalRecords;
        unchecked {
            totalRecords = seq + 1;
            taskRecordCount[taskId] += 1;
        }
        emit AuditRecord(taskId, action, msg.sender, contentHash, block.timestamp, seq);
    }
}
