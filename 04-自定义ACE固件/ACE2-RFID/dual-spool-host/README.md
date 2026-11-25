# 双卷保护：生产快照与离线候选

> **当前状态（2026-11-07）**：CMD13及boot-rescan缓存保护已部署；slot4临时guard已安装但 idle、未 ARM。上次收尾的“未部署”记述是历史阶段。当前等待用户把蓝卷移至4号附近但不插入。部署与可回滚信息见 `deployment-20261107.md`。

`live/` 是2026-11-07设备上下载的源码/配置和只读快照；未覆盖既有子模块。ACE0 UID 808780566/17445170/942880330，97O。fresh四头false、四槽ready，CMD73 INSERT 位 [1,1,1,0]；用户确认界面1槽普通PETG、2槽蓝PLA、3槽粉PLA、4槽空。门状态只表明耗材经过入口，不能独立证明卷或UID。CMD13仅槽3有缓存SKU SME5DDA105和模型长度288873mm；不是本次重新读取的UID证明。

`ace.py` 只针对 `_merge_v2_filament_info` 的CMD13异步回包：每个槽请求独立token，绑定本次merge提前更新的独立槽状态空/非空代次和串口对象（不依赖heartbeat末尾更新的Gen1代次）；空槽清理该槽待处理请求，迟回不得删除更新中的请求或写身份。`cmd13-generation.patch`为相对实际live的focused diff。`test_cmd13_generation.py`提取并执行生产函数：过期重发、移除重插/重连、邻槽独立、真实heartbeat顺序、手动host优先和正常回包；原live在前两项失败，候选通过。未部署。

该修复不解决 heartbeat 未观测到的快速物理换卷、不把单个 reader 能看到的标签证明属于哪个槽，也不修 `_v2_rfid_boot_rescan` 的主动CMD68回调。live `_pair_dedupe_firmware_ids` 在V2直接返回，不能为ACE2提供共享读头保护；`_insert_read_then_preload`、`_insert_tag_read_safe` 使用 `_insert_read_running` 集合去重，同槽重插可能被吞。对原厂预装入口的最小接入需在识别99/97设备时保留native流程、会话代次、STOP监督及排除主动CMD68；两共享槽同时插入时不能从UID或原生缓存推断归属。该工作仍未实现/验收。

## 2026-11-07 部署与下一步候选

CMD13 token/slot epoch 修复已部署到实际运行的ace.py，SHA256 `7e247bd1063cda7fa31b48e99ee0fd026cfbc6fa22aeaf736d387de180d48f02`，备份 `/oem/apps/multiace/rfid-backups/dual-cmd13-20261107T135619/ace.py`（原SHA `fcbd0217f16009a71d253f01a73c85c5b11e9262b2db32c7f5f4a45a9ff51183`）。重启后fresh 97O/UID一致、四lane ready、四head false，主机cfg/proto未改。该补丁不等于完整双卷支持。

`boot-rescan.patch` 基于上述生产版本，准备隔离重连主动CMD68的迟回并在共享pair双占用时跳过该主动scan；`test_boot_rescan.py`覆盖邻槽后插等7例，未部署。仅在host状态观察到变化时隔离迟回，无法检测未上报的物理换卷；CMD13返回也不天然证明物理归属。`guard-slot4/`为针对内部槽3的顺序单目标测试候选，粉卷静置内部槽2，固件自行处理原生队列；27项guard离线测试通过，未部署/ARM。完整同时插入见 `paired-guard-design.md`，尚未实现。
