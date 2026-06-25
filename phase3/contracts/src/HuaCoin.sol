// SPDX-License-Identifier: MIT
pragma solidity ^0.8.21;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/// @title HuaCoin (HUA)
/// @notice 公司内部结算稳定币，1 HUA = 1 人民币，替代 USDC/USDT 作为任务结算货币。
///         标准 18 位精度（前端按 1:1 人民币展示）。
///         由管理员(owner，通常为公司财务/平台账户)按需铸造发行。
contract HuaCoin is ERC20, Ownable {
    event Minted(address indexed to, uint256 amount, string reason);

    constructor() ERC20("HuaCoin", "HUA") Ownable(msg.sender) {}

    /// @notice 铸造华币到指定地址。仅管理员可调用。
    /// @param to 接收地址
    /// @param amount 数量（wei，1 HUA = 1e18）
    function mint(address to, uint256 amount) external onlyOwner {
        _mint(to, amount);
        emit Minted(to, amount, "");
    }

    /// @notice 带事由铸造，便于链下审计对账。
    function mintWithReason(address to, uint256 amount, string calldata reason) external onlyOwner {
        _mint(to, amount);
        emit Minted(to, amount, reason);
    }

    /// @notice 批量铸造（如统一给多名员工发放）。
    function batchMint(address[] calldata recipients, uint256[] calldata amounts) external onlyOwner {
        require(recipients.length == amounts.length, "length mismatch");
        require(recipients.length <= 200, "batch too large");
        for (uint256 i = 0; i < recipients.length; i++) {
            _mint(recipients[i], amounts[i]);
            emit Minted(recipients[i], amounts[i], "batch");
        }
    }
}
