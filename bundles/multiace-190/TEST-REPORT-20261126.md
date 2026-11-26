# MultiACE+ 1.90.2 on U1 — 实机测试报告（2026-11-26）

设备：192.168.2.18，`233screen` 系统，4 × ACE 2 Pro 刷 `ace2k 0.16.0-233b`，
接 `1a86:55d5` 四通道 CDC（`ttyACM0-3`）；另有 4 × `1d50:614e` 为
`[mcu e0..e3]`，与 multiACE 无关。

## 一、结论

| 项 | 结果 |
|---|---|
| 4 台 ACE2K 自动识别 | 通过，4/4 `protocol=ace2k` |
| 连续运行 | 12 小时 23 分无掉线，4/4 `ready` |
| Klipper 重启后恢复 | 通过，UID / 编号 / by-path 不变 |
| USB 掉线重连 | 通过，unbind→bind 后 7 秒 4/4 恢复 |
| 进料（HEAD=0, ACE=0, SLOT=0） | 通过，215 °C，`load_finish`，`at_extruder=True` |
| 退料（HEAD=0） | 通过，`unload_finish`，打印头清空，`in_ace=True` |
| 16 槽状态读取 | 通过（4 台各 4 槽，`ready`/`empty` 与 RFID 均可读） |
| RFID 读取 | **未通过**：slot0 经 600 / 1500 mm 两次搜索均 `host_tag_search_none`，缓存记录仍在 |
| 编码器距离反馈 | **不适用**：`caps.has_decoder=False`（该固件无编码器） |
| 原厂 V2 共存 | **未测试**：现场无 V2 硬件 |
| 烘干 | **未测试**（仅读到 `dryer: stop` 与温湿度） |

## 二、本轮修的两个问题

1. `v2_extra_usb_ids` 中的 `1a86:55d5` 不进自动探测 → 3 个通道被按 USB ID
   直接注册成 V2（230400 无应答，8 s 一次 COMMS_LOST）。改为
   `official or explicitly_configured` 都先做只读双协议握手。
2. 设备表里的 `port:<socket>` 占位在迁移后仍占满 4 个位置，经协议验证的
   UID 设备被拒（`4 places are the maximum`）。改为同 socket 上报 uid 时
   释放该位，保留原 `name`/`kind`。

另修：`ACE_LOAD_HEAD` 判料位用的是扁平 `gate_status` 别名，切换/重连后
到下一次心跳前是 `-1`（未知），被当成"无料"拒绝。改为读目标设备的
`_gate_status_per_ace`，只在 `GATE_EMPTY(0)` 时拒绝。

## 三、部署缺口（不是代码问题）

`klipper/extras/` 必须**整目录**同步，且包内 `filament_feed_ace.py`
要改名成 `filament_feed.py`。只拷 `ace*.py` 时，送料状态机是原厂
102 KB 版本（零处 ACE 逻辑），表现为：

- 上料在 `load_prepare` 直接 `no_filament`；
- "退出耗材"只做一次短回退。

装上 ACE 版后，同一条命令一次通过（见上表）。`install-to-233.sh` 固化此步骤。

## 四、软件回归（Linux 实机，非 mac）

在打印机本机跑 `pytest tests/units tests/test_ace_protocol_v2_probe.py`：

| 树 | 结果 |
|---|---|
| 上游 91ba652 原样 | 82 failed / 495 passed |
| 打补丁后 | 85 failed / 492 passed |

净增 **3 项**（mac 上的 61 项差异确系 `/proc` 缺失，Linux 上不重现）：

- `test_caps.py::test_cap_is_live_on_a_running_fleet`
- `test_contract.py::test_v1_v2_honour_the_contract_with_traffic`
- `test_goldens.py::test_golden[ace2k_keeps_stock_place]`

三项均为自动探测带来的轨迹/能力快照变化，属 golden 未随补丁更新，
不是运行期故障；bundle 自带 74 项测试全绿。

## 五、现场遗留

- 根分区 inode 仅 16384，接近满时 `save_variables` 报 `Errno 28`。
  装依赖务必 `df -i /` 先看。
- RFID 读不到需人工确认：是标签不在读头位置，还是天线/隧道问题。
