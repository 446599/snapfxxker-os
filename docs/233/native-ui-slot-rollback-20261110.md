# U1 原生 UI：系统槽位回退修复

审查基线：`1fdd0ac9ba46b171bc8735b243a585b63e820912`。修复分支：`codex/u1-native-ui-20261110`。固件版本继续使用尚未分发整机包的 `2.1.0-233-dev.3-ui.3`；本次运行包标识递增为 `233-native-ui-20261110.3`。

## 问题与加载链

原启动钩子将 `[u1_native_upgrade]` 写入 `/oem/printer_data/config/extended/klipper/u1_native_upgrade.cfg`。`/oem` 在系统槽位切换后继续保留，而旧 `perms.1` 根文件系统没有对应 Python 模块。正常 V1 配置的加载链为 `S49extended-config` → `S60klipper` → `printer.cfg` → `extended/klipper/*.cfg` → Klipper 模块加载。

独立离线检查使用固定基线中的真实启动脚本、`configfile.py` 和 `Printer.load_object`。原实现的新槽启动成功；同一份 `/oem` 切换到旧槽后，复现 `Section 'u1_native_upgrade' is not a valid config section`。

## 修复

真正的 `[u1_native_upgrade]` 段放在系统镜像内：

```text
/usr/local/share/u1-native-upgrade/klipper.cfg
```

持久配置仅保留允许空匹配的引用：

```ini
[include /usr/local/share/u1-native-upgrade/*.cfg]
```

新槽同时携带配置与模块；旧槽没有匹配文件。已核对的旧 Klipper 加载器允许含通配符的 include 无匹配，因此旧槽不会出现新配置段，也不会尝试加载新模块。该方案不依赖旧启动脚本清理软链接，不修改主 `printer.cfg`，也不改变 `SAVE_CONFIG` 的写入目标。

新钩子先检查升级维护锁，再运行 `slot_config.py`。迁移 `1fdd0ac` 遗留文件时，只将无选项旧段的节头替换为通配 include，保留其他配置段、注释、权限及所有者。原始内容先备份至不以 `.cfg` 结尾的文件，再原子替换并同步落盘，避免备份被旧通配引用再次加载。已正确迁移的文件不重复写入。

未知配置项、含混的缩进节头、符号链接和非普通文件会保留原对象并报告错误。缺少当前槽的配置或模块也会在共享文件写入前失败。维护锁的启动互斥语义保持不变。

## 验证

| 检查 | 结果 |
| --- | --- |
| 源码集成、构建指纹、补丁链、Klipper 交接和槽位配置测试 | 37 项通过 |
| 设备服务、升级状态/并发/恢复和环境代理测试 | 39 项通过 |
| 原实现的新槽 → 旧槽 | 成功复现旧槽缺少模块错误 |
| 修复后的新槽 → 旧槽 → 新槽 | 配置加载均通过；升级端点仅新槽出现 |
| 原坏配置 → 新版迁移 → 旧槽 → 新槽 | 均按预期通过；备份不参与配置加载 |
| 维护锁存在时启动 | 在迁移和加载前拒绝启动 |

合计 76 项 Python 回归通过，较上一轮增加 9 项。独立切槽探针含 10 个步骤，其中旧实现的失败和维护锁拒绝是预期结果。

整机包完成后，又从实际候选 rootfs 提取启动钩子、迁移 helper、共享空闲检查、系统配置片段和 Klipper 升级模块，5 个文件逐字匹配冻结源码，并以这些提取文件重新运行全部 10 个步骤，结果符合预期。验证 ZIP 同时保存源码阶段与实际镜像阶段的结果及可复现探针。

切槽探针在隔离的 Linux 文件系统中运行真实 `S49`、`S60` 与固定配置加载代码，两个系统根复用同一持久目录。硬件命令由无动作替身替代，因此这里验证的是配置与模块加载兼容性，不是实际 MCU 启动。固定基线的 `printer_v2.cfg` 与工厂配置原本不包含这条扩展 include，本次没有扩展其功能。正常系统的目标配置位于 rootfs；启动链不存在将该目录跨槽持久绑定的规则。

旧加载链中的关键文件 SHA-256：

```text
700475ff299a7a0079f416bacb960a15cb99bdf1ccd4215350535403ac709b96  etc/init.d/S60klipper
36163852a7f42dc1af1a1166f745456aad66b40cf25a97106ef849e050be97fc  home/lava/klipper/klippy/configfile.py
0fbd410ef75fcba7c923a0f24d138d8c3245c51768e5018e8b6d19184256ee19  home/lava/klipper/klippy/klippy.py
```

新增系统配置与迁移 helper 都纳入运行包的构建输入指纹：

```text
88a9bf9a9c7636c6b26357360cb1ad6e1950deba22c9d7b1180740bd5702d1dc
```

完整固件、运行包及对应验证材料以 [本次产物索引](native-ui-20261110-artifacts.md) 为准。上一轮三项审查修复保留在 [原修复记录](native-ui-review-fixes-20261110.md)。本轮没有连接、重启或刷写打印机，也没有改动 Mac 隔离工作区。

本次实际整机包为 `U1_233_2.1.0-233-dev.3-ui.3_upgrade.bin`，286,714,394 字节，SHA-256 为 `297401a8cce1191859241b95bed27518f3492a873b900e473f9121ca18648020`。包头、RKFW、RKAF、分区 MD5/CRC 和设备服务的完整包读取校验均通过。rootfs 共 13,187 个路径及 10,278 个普通文件内容完成全量核对，保留 221 组硬链接与 493 个别名；MCU、启动等非 rootfs 组件与固定基线保持一致。
