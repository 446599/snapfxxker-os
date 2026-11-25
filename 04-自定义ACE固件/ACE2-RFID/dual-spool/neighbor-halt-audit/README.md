# 97O 邻槽匹配与 HALT：离线条件回归

`test_branch.py` 在 Unicorn 执行 97 镜像 E346 开始的真实 Thumb，按已知 reader/槽构造 SRAM，比较函数模拟返回相等/不等。邻槽身份匹配时到达 E51A 返回，未进入 E108；邻槽不匹配而当前候选匹配时经 E68E→E6EE→E6FC 到达 E108。测试只验证条件分支和调用，未模拟 RC522、电磁场、调度或真实卡片，不证明现场触发该分支。

DEB6 并非只做 anticollision：DFE0 发送 0x26 REQA，E02C–E048 依次调用 DC50 和 DBA2；DBFC–DC00 构造 0x70 SELECT，级联卡可能重复相应阶段。E308 返回成功后才到 E346，因此在成功的完整单级选择条件下，已执行 SELECT，可处于 ACTIVE；不能把未 HALT 直接解释为下一次 REQA 一定再次选中此卡。E4FA 初始扫描循环和 E6EE 正常路径都调用 E108，E10E 构造 0x50 00 HALT；E378 邻槽命中跳 E514/E51A 则绕开。97 Bambu 解码时的二次 SELECT 与认证位于另一处理路径，认证结束只关闭 Crypto；不能以此推断本邻槽早退路径也 HALT。

E244 的普通下一轮经 E302–E308 以 mode 0 调 DEB6；DEBC–DEC0 显示 mode 0 跳过 DEC2–DF64 初始化，DF68 后对 RC522 配置与 REQA；该直接路径未见主动关闭天线。mode 1 则包含芯片命令与天线寄存器初始化（含仅在未使能时开天线），不等同于已经证明射频场断电重置。其他任务/超时/事件、硬件场衰减与实际卡状态未在此回归建模，不能排除它们改变卡参与下一次 REQA 的状态。

下一项最小证据：在已有受控任务中同时观察每次 SELECT 返回码、选中 UID、worker state 和是否执行 HALT，再与 CMD76 时间序列关联；需先审查只读埋点能否不改时序/不引入共享 scratch 竞争。现有 op10/CMD13 不暴露逐次 UID/分支，不能仅凭缓存 SKU 或计圈为零确定邻卡是根因。避免重复运动试验直至观测方案明确。

运行：`PYTHONPATH=/tmp/ace95-unicorn python3 ace2-rfid-dev-20261107/dual-spool/neighbor-halt-audit/test_branch.py`
