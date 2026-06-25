const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("HuaCoin", function () {
  let hua, owner, alice, bob;

  beforeEach(async function () {
    [owner, alice, bob] = await ethers.getSigners();
    const HuaCoin = await ethers.getContractFactory("HuaCoin");
    hua = await HuaCoin.deploy();
    await hua.waitForDeployment();
  });

  it("名称/符号/精度正确（18 位）", async function () {
    expect(await hua.name()).to.equal("HuaCoin");
    expect(await hua.symbol()).to.equal("HUA");
    expect(await hua.decimals()).to.equal(18);
  });

  it("owner 可铸造", async function () {
    await hua.mint(alice.address, ethers.parseEther("100"));
    expect(await hua.balanceOf(alice.address)).to.equal(ethers.parseEther("100"));
  });

  it("非 owner 铸造应 revert", async function () {
    await expect(
      hua.connect(alice).mint(alice.address, ethers.parseEther("1"))
    ).to.be.revertedWithCustomError(hua, "OwnableUnauthorizedAccount");
  });

  it("mintWithReason 触发 Minted 事件（含事由）", async function () {
    await expect(hua.mintWithReason(alice.address, ethers.parseEther("50"), "salary"))
      .to.emit(hua, "Minted")
      .withArgs(alice.address, ethers.parseEther("50"), "salary");
  });

  it("batchMint 批量铸造", async function () {
    await hua.batchMint(
      [alice.address, bob.address],
      [ethers.parseEther("10"), ethers.parseEther("20")]
    );
    expect(await hua.balanceOf(alice.address)).to.equal(ethers.parseEther("10"));
    expect(await hua.balanceOf(bob.address)).to.equal(ethers.parseEther("20"));
  });

  it("batchMint 长度不匹配应 revert", async function () {
    await expect(hua.batchMint([alice.address], [1, 2])).to.be.revertedWith(
      "length mismatch"
    );
  });

  it("可作为 ERC20 转账（模拟结算）", async function () {
    await hua.mint(alice.address, ethers.parseEther("100"));
    await hua.connect(alice).transfer(bob.address, ethers.parseEther("30"));
    expect(await hua.balanceOf(bob.address)).to.equal(ethers.parseEther("30"));
    expect(await hua.balanceOf(alice.address)).to.equal(ethers.parseEther("70"));
  });
});
