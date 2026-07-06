"""[已退役] 自治任务全链路端到端测试（自动撮合 + 自动执行）。

任务类型全面 SE 化后（2026-07-05），本脚本验证的链路已按设计下线：
  - task_matcher 自动撮合明确排除 SE 任务（全部任务均为 SE 类型 → 实质 no-op），
    自动派单由 se_marketplace cron 的自主竞价承担（tests/e2e_se_bidding.py 覆盖）；
  - SE 任务不进平台自动执行队列，由中标智能体自行交付
    （tests/e2e_se_full.py 覆盖 交付 → 3 专家评审 → 华币 + NAU 全流程）。

保留本文件仅作历史路标；直接运行输出 SKIPPED 并以 0 退出。
原实现见 git 历史（提交 5284417 之前版本）。
"""
import sys

print("SKIPPED: 自动撮合+自动执行链路已随任务类型全面 SE 化退役；"
      "竞价派单见 e2e_se_bidding.py，交付评审奖励见 e2e_se_full.py")
sys.exit(0)
