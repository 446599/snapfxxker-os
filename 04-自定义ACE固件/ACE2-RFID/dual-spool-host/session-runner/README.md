# 离线候选：同一进程多轮 guard

基于 guard-slot4-trace；仅新增 `ACE_NATIVE_GUARD_REARM TARGET=...`，不改变运动或协议。confirmed 后要求同一打开的 transport、无停止故障、STOP ACK、当前至少 1.5 秒稳定且 0.5 秒内新鲜的 decoder、新鲜四槽 READY、四头均 false、没有在途查询和插入/读卡工作。验证通过后不注销定时器，单个命令直接切换到 waiting_empty 并清理本轮 trace；需目标清空后才接受插入。旧回包通过 event_seq/token 隔离。REARM 不处理 active/stopping/fault。

候选未部署。驱动应先保存前一轮完整 trace，再等待可重试的 REARM 检查窗口；REARM 或 ARM 回复超时不表示命令失败，先查询状态，不盲目重发运动。确认新一轮 ARM 前后设备 UID/版本与连接会话；若 transport 改变，重做全套安全确认。confirmed 的状态轮询继续刷新停车证据；静止等待且没有 pending 查询时可 REARM。连接状态与读卡队列在每次检查时核实。

流程节约：一次安装、重启与身份基线确认后，连续轮次共享同一 transport、保护器和旁路端口 hold，轮次之间保存 trace → 停稳确认 → 条件满足后 REARM。只对明确为只读查询且超时的请求有限重试；STOP、写操作或无明确结果的命令不因超时自动重发。最终结束再由操作员核验并恢复临时配置/端口。提高退料步长或速度需另行根据净空、传感器和实际停距做受控方案，当前候选不改变退料。

运行：`python3 -m unittest discover -s ace2-rfid-dev-20261107/dual-spool-host/session-runner -p test_reset.py -v`。

2026-11-07：已部署 guard SHA `2ec38a6f0ebb24b10ad621dead1db383ef856a763975fb880f7c98a3c90d0caa`，服务重启后 idle；idle 下 REARM 返回 `requires confirmed stop`，状态仍 idle。fresh ACE 97O 同 UID、四槽 ready、四头 false、RF [1,1]、holds false。前一 guard 与临时 cfg 备份于设备 `/tmp/session-runner-backup/`，原生产 cfg 在 `/tmp/dual-slot4-trace-backup/printer.cfg`；主机 ace.py/proto 未修改。尚未对真实 confirmed→REARM 多轮进行实机验证，不把离线通过或 idle 拒绝当成该验证。

`session_driver.py` 是未来轮次候选，必须显式 `--execute`，每轮新目录；当前未执行。只读超时 helper 检查命令码，并仅在已识别的设备超时提示时有限重试一次；运动不重发。每轮执行前仍须先保存上一轮完整 telemetry 文件，不能仅凭内存 ring 视为已归档。
